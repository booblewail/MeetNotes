from collections.abc import AsyncGenerator
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


def create_engine_for_url(database_url: str) -> AsyncEngine:
    kwargs: dict = {"echo": False, "future": True}
    if database_url.startswith("sqlite"):
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    return create_async_engine(database_url, **kwargs)


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
