from __future__ import annotations

from collections import OrderedDict
import hashlib
import logging
import math
import re
import time

from google.genai import types

from app.core.config import settings
from app.core.genai_client import get_genai_client

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
_QUERY_CACHE_MAX = 256
_QUERY_CACHE_TTL_SECONDS = 600.0
_QUERY_EMBED_CACHE: OrderedDict[
    tuple[str, str, bool], tuple[float, list[float]]
] = OrderedDict()


def clear_query_embedding_cache() -> None:
    """Clear the bounded search query embedding cache."""
    _QUERY_EMBED_CACHE.clear()

_SYNONYM_GROUPS: dict[str, list[str]] = {
    "auth": ["authentication", "authorize", "oauth", "jwt", "login", "token", "security"],
    "authentication": ["auth", "oauth", "jwt", "login", "token"],
    "oauth": ["auth", "authentication", "jwt", "sso", "login"],
    "jwt": ["auth", "authentication", "token", "session"],
    "database": ["postgres", "pgvector", "sql", "schema", "migration"],
    "postgres": ["database", "pgvector", "sql", "alembic"],
    "search": ["hybrid", "rrf", "vector", "keyword", "query"],
    "latency": ["performance", "speed", "realtime", "streaming", "websocket"],
    "mobile": ["pwa", "responsive", "ios", "android", "wakelock"],
    "privacy": ["redact", "redaction", "retention", "delete", "purge"],
    "budget": ["pricing", "cost", "spend", "forecast", "finance"],
    "launch": ["release", "rollout", "ship", "milestone", "deadline"],
}


def deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic normalized 768-dim embedding for offline/mock semantic similarity."""
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    if not tokens:
        vec[0] = 1.0
        return vec

    expanded: list[tuple[str, float]] = []
    for tok in tokens:
        expanded.append((tok, 1.0))
        if len(tok) > 4:
            expanded.append((tok[:4], 0.55))
        for syn in _SYNONYM_GROUPS.get(tok, []):
            expanded.append((syn, 0.7))
    for i in range(len(tokens) - 1):
        expanded.append((f"{tokens[i]}_{tokens[i + 1]}", 1.25))

    for feature, weight in expanded:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    else:
        vec[0] = 1.0
    return vec


def cosine_similarity(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate 768-dim embeddings using Gemini or deterministic local fallback."""
    if not texts:
        return []
    if settings.is_mock_llm:
        return [deterministic_embedding(t) for t in texts]

    try:
        client = get_genai_client(timeout_ms=15_000)
        result = await client.aio.models.embed_content(
            model=settings.EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
        )
        embeddings = result.embeddings or []
        if len(embeddings) == len(texts):
            return [list(e.values or deterministic_embedding(t)) for e, t in zip(embeddings, texts)]
    except Exception as exc:
        logger.warning("Gemini embed_content failed (%s); falling back to local embeddings", exc)

    return [deterministic_embedding(t) for t in texts]


async def embed_query(query: str) -> list[float]:
    """Embed a search query using a bounded TTL LRU cache."""
    normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
    cache_key = (normalized, settings.EMBEDDING_MODEL, settings.is_mock_llm)
    now = time.monotonic()

    cached = _QUERY_EMBED_CACHE.get(cache_key)
    if cached is not None:
        ts, vec = cached
        if now - ts <= _QUERY_CACHE_TTL_SECONDS:
            _QUERY_EMBED_CACHE.move_to_end(cache_key)
            return vec
        _QUERY_EMBED_CACHE.pop(cache_key, None)

    vec = (await embed_texts([query]))[0]
    _QUERY_EMBED_CACHE[cache_key] = (now, vec)
    if len(_QUERY_EMBED_CACHE) > _QUERY_CACHE_MAX:
        _QUERY_EMBED_CACHE.popitem(last=False)
    return vec
