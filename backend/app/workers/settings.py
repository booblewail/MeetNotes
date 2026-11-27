from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.redis import broker
from app.services.storage import audio_storage
from app.workers.tasks import finalize_session_task, purge_expired_audio_task


async def startup(ctx: dict) -> None:
    await broker.init()
    await audio_storage.ensure_bucket()


async def shutdown(ctx: dict) -> None:
    await broker.close()


class WorkerSettings:
    functions = [finalize_session_task, purge_expired_audio_task]
    cron_jobs = [cron(purge_expired_audio_task, minute=0)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 10
    job_timeout = 300
