from __future__ import annotations

import asyncio
import weakref

from google import genai
from google.genai import types

from app.core.config import settings

AFC_DISABLED = types.AutomaticFunctionCallingConfig(disable=True)

_CLIENTS_BY_LOOP: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, int], genai.Client]
] = weakref.WeakKeyDictionary()


def get_genai_client(timeout_ms: int = 30_000) -> genai.Client:
    """Return a loop-scoped cached Gemini client reusing HTTP/TLS connections."""
    api_key = settings.GEMINI_API_KEY
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )

    per_loop = _CLIENTS_BY_LOOP.get(loop)
    if per_loop is None:
        per_loop = {}
        _CLIENTS_BY_LOOP[loop] = per_loop

    cache_key = (api_key, timeout_ms)
    client = per_loop.get(cache_key)
    if client is None:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        per_loop[cache_key] = client
    return client
