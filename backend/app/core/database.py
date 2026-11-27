from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy ORM models."""


def normalize_async_database_url(database_url: str) -> tuple[str, dict]:
    """Rewrite Neon/Render/Supabase Postgres URLs for asyncpg compatibility."""
    url = database_url.strip()
    if url.startswith("sqlite"):
        return url, {"check_same_thread": False}

    for prefix in ("postgres://", "postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
            break

    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    connect_args: dict = {}
    kept_params: list[tuple[str, str]] = []

    for key, val in params:
        lower = key.lower()
        if lower == "sslmode":
            if val.lower() in {"require", "verify-ca", "verify-full", "prefer"}:
                connect_args["ssl"] = "require"
        elif lower == "channel_binding":
            # asyncpg does not accept libpq's channel_binding keyword arg
            continue
        else:
            kept_params.append((key, val))

    clean_url = urlunsplit(parts._replace(query=urlencode(kept_params)))
    return clean_url, connect_args


def create_engine_for_url(
    database_url: str, *, use_null_pool: bool = False
) -> AsyncEngine:
    clean_url, connect_args = normalize_async_database_url(database_url)
    kwargs: dict = {"echo": False, "future": True}
    if connect_args:
        kwargs["connect_args"] = connect_args

    if clean_url.startswith("sqlite") or use_null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    return create_async_engine(clean_url, **kwargs)


engine: AsyncEngine = create_engine_for_url(settings.DATABASE_URL)
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def configure_database(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Reconfigure engine and session factory (used by tests and isolated runners)."""
    global engine
    engine = create_engine_for_url(database_url)
    AsyncSessionLocal.configure(bind=engine)
    return AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
