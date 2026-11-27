from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


class _InMemoryState:
    """Thread/loop-safe in-memory fallback for tests or environments without Redis."""

    def __init__(self) -> None:
        self.channels: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.kv: dict[str, tuple[str, float | None]] = {}
        self.locks: dict[str, float] = {}

    def reset(self) -> None:
        self.channels.clear()
        self.lists.clear()
        self.kv.clear()
        self.locks.clear()


class RedisBroker:
    """Async Redis + Pub/Sub + Lock helper with automatic in-memory fallback."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._arq_pool: ArqRedis | None = None
        self._use_memory: bool = settings.REDIS_URL.startswith("memory://")
        self.memory = _InMemoryState()

    def use_memory_backend(self, enabled: bool = True) -> None:
        self._use_memory = enabled
        if enabled:
            self.memory.reset()

    async def init(self) -> None:
        if self._use_memory or settings.REDIS_URL.startswith("memory://"):
            self._use_memory = True
            return
        try:
            client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2.0,
            )
            await client.ping()
            self._client = client
            self._use_memory = False
        except Exception as exc:
            logger.warning(
                "Redis unavailable (%s); falling back to in-memory broker", exc
            )
            self._client = None
            self._use_memory = True

    async def close(self) -> None:
        if self._arq_pool is not None:
            await self._arq_pool.aclose()
            self._arq_pool = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_client(self) -> aioredis.Redis | None:
        if self._use_memory:
            return None
        if self._client is None:
            await self.init()
        return self._client

    async def get_arq_pool(self) -> ArqRedis | None:
        if self._use_memory or settings.REDIS_URL.startswith("memory://"):
            return None
        if self._arq_pool is not None:
            return self._arq_pool
        try:
            self._arq_pool = await create_pool(
                RedisSettings.from_dsn(settings.REDIS_URL)
            )
            return self._arq_pool
        except Exception as exc:
            logger.warning("Unable to connect ARQ Redis pool: %s", exc)
            return None

    @staticmethod
    def channel_name(session_id: str) -> str:
        return f"session:{session_id}:events"

    @staticmethod
    def pending_list_key(session_id: str) -> str:
        return f"session:{session_id}:pending_utterances"

    @staticmethod
    def pass1_lock_key(session_id: str) -> str:
        return f"session:{session_id}:pass1_lock"

    @staticmethod
    def pass1_last_ts_key(session_id: str) -> str:
        return f"session:{session_id}:pass1_last_ts"

    async def publish_session_event(
        self, session_id: str, event: dict[str, Any]
    ) -> None:
        channel = self.channel_name(session_id)
        client = await self.get_client()
        if client is not None:
            try:
                await client.publish(channel, json.dumps(event))
                return
            except Exception as exc:
                logger.warning("Redis publish failed, falling back to memory: %s", exc)
        # Always fan out to in-memory subscribers when active
        for queue in list(self.memory.channels.get(channel, set())):
            await queue.put(event)

    async def subscribe_session_events(
        self, session_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        channel = self.channel_name(session_id)
        client = await self.get_client()
        if client is not None:
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
            try:
                while True:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if msg and msg.get("data"):
                        try:
                            yield json.loads(msg["data"])
                        except json.JSONDecodeError:
                            continue
                    else:
                        await asyncio.sleep(0.05)
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.memory.channels[channel].add(queue)
        try:
            while True:
                item = await queue.get()
                yield item
        finally:
            self.memory.channels[channel].discard(queue)

    async def push_pending_utterance(
        self, session_id: str, utterance: dict[str, Any]
    ) -> int:
        key = self.pending_list_key(session_id)
        payload = json.dumps(utterance)
        client = await self.get_client()
        if client is not None:
            return int(await client.rpush(key, payload))
        self.memory.lists[key].append(payload)
        return len(self.memory.lists[key])

    async def get_pending_utterances(self, session_id: str) -> list[dict[str, Any]]:
        key = self.pending_list_key(session_id)
        client = await self.get_client()
        if client is not None:
            raw_items = await client.lrange(key, 0, -1)
        else:
            raw_items = list(self.memory.lists[key])
        return [json.loads(item) for item in raw_items]

    async def pop_pending_utterances(self, session_id: str, count: int) -> None:
        if count <= 0:
            return
        key = self.pending_list_key(session_id)
        client = await self.get_client()
        if client is not None:
            await client.ltrim(key, count, -1)
            return
        self.memory.lists[key] = self.memory.lists[key][count:]

    async def clear_pending_utterances(self, session_id: str) -> None:
        key = self.pending_list_key(session_id)
        client = await self.get_client()
        if client is not None:
            await client.delete(key)
            return
        self.memory.lists.pop(key, None)

    async def get_last_pass1_ts(self, session_id: str) -> float | None:
        key = self.pass1_last_ts_key(session_id)
        client = await self.get_client()
        if client is not None:
            raw = await client.get(key)
            return float(raw) if raw is not None else None
        entry = self.memory.kv.get(key)
        return float(entry[0]) if entry else None

    async def set_last_pass1_ts(self, session_id: str, ts: float | None = None) -> None:
        val = str(ts if ts is not None else time.monotonic())
        key = self.pass1_last_ts_key(session_id)
        client = await self.get_client()
        if client is not None:
            await client.set(key, val, ex=86400)
            return
        self.memory.kv[key] = (val, None)

    async def acquire_pass1_lock(
        self, session_id: str, ttl_seconds: int = 25
    ) -> bool:
        key = self.pass1_lock_key(session_id)
        client = await self.get_client()
        if client is not None:
            return bool(await client.set(key, "1", nx=True, ex=ttl_seconds))
        now = time.monotonic()
        expiry = self.memory.locks.get(key)
        if expiry is not None and expiry > now:
            return False
        self.memory.locks[key] = now + ttl_seconds
        return True

    async def release_pass1_lock(self, session_id: str) -> None:
        key = self.pass1_lock_key(session_id)
        client = await self.get_client()
        if client is not None:
            await client.delete(key)
            return
        self.memory.locks.pop(key, None)


broker = RedisBroker()
