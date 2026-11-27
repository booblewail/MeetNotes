from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import ClientConnection

from app.core.config import settings

logger = logging.getLogger(__name__)

BYTES_PER_MS = 32  # 16kHz * 1 channel * 2 bytes/sample


@dataclass
class STTWord:
    word: str
    start_ms: int
    end_ms: int
    confidence: float
    speaker: int | None = None


@dataclass
class STTTranscriptEvent:
    text: str
    speaker_index: int
    start_ms: int
    end_ms: int
    confidence: float
    words: list[STTWord] = field(default_factory=list)
    is_final: bool = True


class LiveSTTStream(Protocol):
    mode: str

    async def start(self) -> None: ...
    async def send_audio(self, offset_ms: int, pcm_bytes: bytes) -> None: ...
    async def next_event(self) -> STTTranscriptEvent | None: ...
    async def close(self) -> None: ...


class _PiecewiseTimelineMapper:
    """Maps continuous Deepgram stream milliseconds back to meeting offset_ms across muted gaps."""

    def __init__(self) -> None:
        self.stream_cursor_ms: int = 0
        self.segments: list[tuple[int, int, int]] = []  # (stream_start, stream_end, meeting_offset_start)

    def record_chunk(self, meeting_offset_ms: int, pcm_len: int) -> None:
        dur_ms = max(1, pcm_len // BYTES_PER_MS)
        s_start = self.stream_cursor_ms
        s_end = s_start + dur_ms
        self.segments.append((s_start, s_end, meeting_offset_ms))
        if len(self.segments) > 4000:
            self.segments = self.segments[-2000:]
        self.stream_cursor_ms = s_end

    def to_meeting_ms(self, stream_ms: int) -> int:
        if not self.segments:
            return max(0, stream_ms)
        for s_start, s_end, m_start in reversed(self.segments):
            if stream_ms >= s_start:
                delta = max(0, stream_ms - s_start)
                return m_start + min(delta, (s_end - s_start) + 2000)
        return max(0, stream_ms)


class DeepgramLiveStream:
    """Direct async WebSocket client for Deepgram Nova-3 live diarized transcription."""

    mode = "deepgram"

    def __init__(self, keyterms: list[str] | None = None) -> None:
        self.keyterms = [k.strip() for k in (keyterms or []) if k.strip()][:50]
        self._ws: ClientConnection | None = None
        self._queue: asyncio.Queue[STTTranscriptEvent | None] = asyncio.Queue()
        self._mapper = _PiecewiseTimelineMapper()
        self._recv_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._closed = False

    def _build_url(self) -> str:
        params: list[tuple[str, str]] = [
            ("model", "nova-3"),
            ("encoding", "linear16"),
            ("sample_rate", "16000"),
            ("channels", "1"),
            ("diarize", "true"),
            ("smart_format", "true"),
            ("interim_results", "true"),
            ("utterance_end_ms", "1000"),
            ("vad_events", "true"),
        ]
        for term in self.keyterms:
            params.append(("keyterm", term))
        return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"

    async def start(self) -> None:
        url = self._build_url()
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            ping_interval=20,
            ping_timeout=20,
        )
        self._recv_task = asyncio.create_task(self._receive_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closed and self._ws is not None:
                await asyncio.sleep(5.0)
                if self._ws is not None and not self._closed:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("Deepgram keepalive stopped: %s", exc)

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") != "Results":
                    continue
                channel = msg.get("channel") or {}
                alts = channel.get("alternatives") or []
                if not alts:
                    continue
                alt = alts[0]
                transcript = (alt.get("transcript") or "").strip()
                if not transcript:
                    continue

                is_final = bool(msg.get("is_final"))
                start_s = float(msg.get("start") or 0.0)
                dur_s = float(msg.get("duration") or 0.0)
                raw_words = alt.get("words") or []

                words: list[STTWord] = []
                speaker_votes: dict[int, int] = {}
                for w in raw_words:
                    sp = w.get("speaker")
                    sp_idx = int(sp) if sp is not None else 0
                    speaker_votes[sp_idx] = speaker_votes.get(sp_idx, 0) + 1
                    w_start_ms = self._mapper.to_meeting_ms(int(float(w.get("start", start_s)) * 1000))
                    w_end_ms = self._mapper.to_meeting_ms(int(float(w.get("end", start_s + dur_s)) * 1000))
                    words.append(
                        STTWord(
                            word=str(w.get("punctuated_word") or w.get("word") or ""),
                            start_ms=w_start_ms,
                            end_ms=max(w_start_ms, w_end_ms),
                            confidence=float(w.get("confidence", alt.get("confidence", 0.95))),
                            speaker=sp_idx,
                        )
                    )

                dominant_speaker = (
                    max(speaker_votes.items(), key=lambda kv: kv[1])[0]
                    if speaker_votes
                    else 0
                )
                seg_start_ms = self._mapper.to_meeting_ms(int(start_s * 1000))
                seg_end_ms = max(
                    seg_start_ms + 250,
                    self._mapper.to_meeting_ms(int((start_s + dur_s) * 1000)),
                )
                confidence = float(alt.get("confidence", 0.95))
                if words:
                    confidence = min(confidence, sum(w.confidence for w in words) / len(words))

                await self._queue.put(
                    STTTranscriptEvent(
                        text=transcript,
                        speaker_index=dominant_speaker,
                        start_ms=seg_start_ms,
                        end_ms=seg_end_ms,
                        confidence=round(confidence, 4),
                        words=words,
                        is_final=is_final,
                    )
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Deepgram receive loop ended: %s", exc)
        finally:
            await self._queue.put(None)

    async def send_audio(self, offset_ms: int, pcm_bytes: bytes) -> None:
        if self._ws is None or self._closed or not pcm_bytes:
            return
        self._mapper.record_chunk(offset_ms, len(pcm_bytes))
        await self._ws.send(pcm_bytes)

    async def next_event(self) -> STTTranscriptEvent | None:
        return await self._queue.get()

    async def close(self) -> None:
        self._closed = True
        if self._keepalive_task:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
        await self._queue.put(None)


class MockLiveStream:
    """Zero-key local STT stream supporting manual simulation and gentle live audio echo."""

    mode = "mock"

    def __init__(self, keyterms: list[str] | None = None) -> None:
        self.keyterms = keyterms or []
        self._queue: asyncio.Queue[STTTranscriptEvent | None] = asyncio.Queue()
        self._closed = False
        self._bytes_since_interim = 0

    async def start(self) -> None:
        return None

    async def send_audio(self, offset_ms: int, pcm_bytes: bytes) -> None:
        if self._closed or not pcm_bytes:
            return
        # Emit an unobtrusive interim heartbeat when live mic PCM arrives in mock mode
        self._bytes_since_interim += len(pcm_bytes)
        if self._bytes_since_interim >= 32_000:  # ~1 second of 16kHz PCM16
            self._bytes_since_interim = 0
            await self._queue.put(
                STTTranscriptEvent(
                    text="Listening (Deepgram mock mode — use Demo Conversation or set DEEPGRAM_API_KEY)…",
                    speaker_index=0,
                    start_ms=offset_ms,
                    end_ms=offset_ms + 1000,
                    confidence=0.95,
                    words=[],
                    is_final=False,
                )
            )

    async def inject_utterance(
        self,
        text: str,
        speaker_index: int,
        start_ms: int,
        end_ms: int,
        confidence: float = 0.96,
    ) -> None:
        words_raw = text.split()
        dur = max(200, end_ms - start_ms)
        step = max(50, dur // max(1, len(words_raw)))
        words = [
            STTWord(
                word=w,
                start_ms=start_ms + idx * step,
                end_ms=start_ms + (idx + 1) * step,
                confidence=confidence,
                speaker=speaker_index,
            )
            for idx, w in enumerate(words_raw)
        ]
        await self._queue.put(
            STTTranscriptEvent(
                text=text,
                speaker_index=speaker_index,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=confidence,
                words=words,
                is_final=True,
            )
        )

    async def next_event(self) -> STTTranscriptEvent | None:
        return await self._queue.get()

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)


def create_stt_stream(keyterms: list[str] | None = None) -> DeepgramLiveStream | MockLiveStream:
    if settings.is_mock_stt:
        return MockLiveStream(keyterms=keyterms)
    return DeepgramLiveStream(keyterms=keyterms)
