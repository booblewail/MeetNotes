from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Session
from app.services.consolidation import consolidate_session
from app.services.storage import audio_storage

logger = logging.getLogger(__name__)


async def finalize_session_task(ctx: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Run Pass 2 consolidation for a completed meeting session."""
    logger.info("Running Pass 2 finalize_session_task for %s", session_id)
    event = await consolidate_session(session_id)
    if event is None:
        return {"session_id": session_id, "status": "not_found"}
    return {
        "session_id": session_id,
        "status": event.status,
        "sections": len(event.sections),
        "blocks": len(event.blocks),
    }


async def purge_expired_audio_task(ctx: dict[str, Any]) -> int:
    """Delete expired S3/MinIO audio files according to each session's audio_expires_at."""
    now = datetime.now(UTC)
    purged = 0
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Session).where(
                Session.audio_s3_key.is_not(None),
                Session.audio_expires_at.is_not(None),
                Session.audio_expires_at <= now,
            )
        )
        expired_sessions = list(res.scalars().all())
        for sess in expired_sessions:
            if sess.audio_s3_key:
                await audio_storage.delete_object(sess.audio_s3_key)
                sess.audio_s3_key = None
                sess.audio_expires_at = None
                purged += 1
        if purged:
            await db.commit()
    logger.info("purge_expired_audio_task purged %d expired audio object(s)", purged)
    return purged
