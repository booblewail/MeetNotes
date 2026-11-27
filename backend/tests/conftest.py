from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Configure isolated test settings BEFORE importing app modules
_TEST_TMP = Path(tempfile.mkdtemp(prefix="meetnotes_test_"))
_RUN_LIVE_AI = os.environ.get("RUN_LIVE_AI_TESTS") == "1"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_TMP / 'test.db'}"
os.environ["REDIS_URL"] = "memory://"
if not _RUN_LIVE_AI:
    os.environ["DEEPGRAM_API_KEY"] = "mock"
    os.environ["GEMINI_API_KEY"] = "mock"
os.environ["AUDIO_SPOOL_DIR"] = str(_TEST_TMP / "spool")
os.environ["LOCAL_S3_FALLBACK_DIR"] = str(_TEST_TMP / "s3")
os.environ["S3_ENDPOINT_URL"] = "http://127.0.0.1:59999"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core import database  # noqa: E402
from app.core.redis import broker  # noqa: E402
import app.models  # noqa: F401, E402
from app.main import app as fastapi_app  # noqa: E402
from app.services.consolidation import clear_export_cache  # noqa: E402
from app.services.embeddings import clear_query_embedding_cache  # noqa: E402
from app.services.storage import audio_storage  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_test_db_and_broker(tmp_path: Path) -> AsyncGenerator[None, None]:
    db_file = tmp_path / "meetnotes_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    database.configure_database(db_url)
    broker.use_memory_backend(True)
    audio_storage._s3_available = False
    clear_export_cache()
    clear_query_embedding_cache()

    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.drop_all)
        await conn.run_sync(database.Base.metadata.create_all)

    yield

    await database.engine.dispose()
    broker.memory.reset()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_user(client: AsyncClient) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": "alice@meetnotes.dev",
            "password": "supersecretpassword",
            "full_name": "Alice Chen",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "token": data["access_token"],
        "user": data["user"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }
