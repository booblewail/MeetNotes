from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, blocks, glossary, search, sessions, ws
from app.core.config import settings
from app.core.redis import broker
from app.services.storage import audio_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    await broker.init()
    await audio_storage.ensure_bucket()
    try:
        yield
    finally:
        await broker.close()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "stt_mode": "mock" if settings.is_mock_stt else "deepgram",
        "llm_mode": "mock" if settings.is_mock_llm else "gemini",
    }


app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(blocks.router)
app.include_router(glossary.router)
app.include_router(search.router)
app.include_router(ws.router)
