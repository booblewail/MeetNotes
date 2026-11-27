from __future__ import annotations

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.serializers import (
    serialize_session_detail,
    serialize_session_summary,
    serialize_speaker,
)
from app.core.database import get_db
from app.core.redis import broker
from app.models import (
    Session,
    SessionSpeaker,
    User,
)
from app.schemas.api_schemas import (
    AudioUrlResponse,
    ExportRequest,
    ExportResponse,
    PinMomentRequest,
    RedactWindowRequest,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionSummaryResponse,
    SessionUpdateRequest,
    SpeakerRenameRequest,
)
from app.schemas.ws_events import (
    MomentPinnedEvent,
    PinnedMomentPayload,
    SessionRedactedEvent,
    SessionStatusEvent,
    SpeakerPayload,
    SpeakerUpdatedEvent,
)
from app.services.consolidation import (
    enqueue_or_run_consolidation,
    export_session_content,
    persist_session_audio_from_spool,
    redact_session_window,
)
from app.services.storage import audio_storage

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _load_full_session(
    session_id: str, user_id: str, db: AsyncSession
) -> Session:
    result = await db.execute(
        select(Session)
        .where(Session.id == session_id, Session.user_id == user_id)
        .options(
            selectinload(Session.speakers),
            selectinload(Session.segments),
            selectinload(Session.sections),
            selectinload(Session.blocks),
            selectinload(Session.action_items),
        )
        .execution_options(populate_existing=True)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


@router.get("", response_model=list[SessionSummaryResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionSummaryResponse]:
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id)
        .options(
            selectinload(Session.sections),
            selectinload(Session.blocks),
            selectinload(Session.action_items),
        )
        .order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()
    return [serialize_session_summary(s) for s in sessions]


@router.post(
    "", response_model=SessionDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_session(
    payload: SessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    session = Session(
        user_id=current_user.id,
        title=payload.title.strip() or "Untitled Meeting",
        status="created",
        audio_retention=payload.audio_retention or current_user.default_audio_retention,
        pinned_moments=[],
    )
    db.add(session)
    await db.commit()
    loaded = await _load_full_session(session.id, current_user.id, db)
    return serialize_session_detail(loaded)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    session = await _load_full_session(session_id, current_user.id, db)
    return serialize_session_detail(session)


@router.patch("/{session_id}", response_model=SessionDetailResponse)
async def update_session(
    session_id: str,
    payload: SessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    session = await _load_full_session(session_id, current_user.id, db)
    if payload.title is not None:
        session.title = payload.title.strip() or session.title
    if payload.executive_summary is not None:
        session.executive_summary = payload.executive_summary
    if payload.audio_retention is not None:
        session.audio_retention = payload.audio_retention
    await db.commit()
    loaded = await _load_full_session(session_id, current_user.id, db)
    return serialize_session_detail(loaded)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    s3_key = session.audio_s3_key
    await db.delete(session)
    await db.commit()
    if s3_key:
        await audio_storage.delete_object(s3_key)
    audio_storage.delete_spool_file(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{session_id}/speakers/{speaker_id}", response_model=SpeakerPayload
)
async def rename_speaker(
    session_id: str,
    speaker_id: str,
    payload: SpeakerRenameRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SpeakerPayload:
    await _load_full_session(session_id, current_user.id, db)
    res = await db.execute(
        select(SessionSpeaker).where(
            SessionSpeaker.id == speaker_id,
            SessionSpeaker.session_id == session_id,
        )
    )
    speaker = res.scalar_one_or_none()
    if speaker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Speaker not found"
        )
    speaker.display_name = payload.display_name.strip()
    await db.commit()
    await db.refresh(speaker)
    speaker_dto = serialize_speaker(speaker)
    await broker.publish_session_event(
        session_id,
        SpeakerUpdatedEvent(session_id=session_id, speaker=speaker_dto).model_dump(),
    )
    return speaker_dto


@router.post("/{session_id}/pin-moment", response_model=PinnedMomentPayload)
async def pin_moment(
    session_id: str,
    payload: PinMomentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PinnedMomentPayload:
    result = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    moment = PinnedMomentPayload(
        offset_ms=payload.offset_ms,
        label=payload.label.strip() if payload.label else None,
        created_at=datetime.now(UTC).isoformat(),
    )
    moments = list(session.pinned_moments or [])
    moments.append(moment.model_dump())
    session.pinned_moments = moments
    await db.commit()
    await broker.publish_session_event(
        session_id,
        MomentPinnedEvent(session_id=session_id, moment=moment).model_dump(),
    )
    return moment


@router.post("/{session_id}/redact-window", response_model=SessionRedactedEvent)
async def redact_window(
    session_id: str,
    payload: RedactWindowRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionRedactedEvent:
    await _load_full_session(session_id, current_user.id, db)
    event = await redact_session_window(
        db=db,
        session_id=session_id,
        window_ms=payload.window_ms,
        current_offset_ms=payload.current_offset_ms,
    )
    await broker.publish_session_event(session_id, event.model_dump())
    return event


@router.post("/{session_id}/stop", response_model=SessionDetailResponse)
async def stop_session(
    session_id: str,
    run_inline: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    session = await _load_full_session(session_id, current_user.id, db)
    if session.status in ("created", "recording", "paused"):
        session.status = "processing"
        await persist_session_audio_from_spool(db, session)
        await db.commit()
        await broker.publish_session_event(
            session_id,
            SessionStatusEvent(
                session_id=session_id,
                status="processing",
                duration_ms=session.duration_ms,
            ).model_dump(),
        )
        await enqueue_or_run_consolidation(session_id, run_inline=run_inline)
    loaded = await _load_full_session(session_id, current_user.id, db)
    return serialize_session_detail(loaded)


@router.get("/{session_id}/audio-url", response_model=AudioUrlResponse)
async def get_session_audio_url(
    session_id: str,
    request: Request,
    token: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AudioUrlResponse:
    session = await _load_full_session(session_id, current_user.id, db)
    if not session.audio_s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio is not stored for this session",
        )
    if session.audio_expires_at and session.audio_expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Audio retention period has expired",
        )
    auth_header = request.headers.get("authorization", "")
    raw_token = token or (
        auth_header.removeprefix("Bearer ").strip()
        if auth_header.startswith("Bearer ")
        else ""
    )
    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/sessions/{session_id}/audio?token={raw_token}"
    presigned = await audio_storage.generate_presigned_get_url(session.audio_s3_key)
    return AudioUrlResponse(
        url=presigned or stream_url,
        stream_url=stream_url,
        expires_at=session.audio_expires_at,
    )


@router.get("/{session_id}/audio")
async def stream_session_audio(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    session = await _load_full_session(session_id, current_user.id, db)
    if not session.audio_s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio is not stored for this session",
        )
    data = await audio_storage.get_audio_bytes(session.audio_s3_key, session_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Audio file not found"
        )

    total_len = len(data)
    range_header = request.headers.get("range")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "audio/wav",
        "Cache-Control": "private, max-age=300",
    }
    if range_header and range_header.startswith("bytes="):
        spec = range_header.replace("bytes=", "").split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else total_len - 1
        start = max(0, min(start, total_len - 1))
        end = max(start, min(end, total_len - 1))
        chunk = data[start : end + 1]
        headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
        headers["Content-Length"] = str(len(chunk))
        return Response(
            content=chunk,
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            headers=headers,
            media_type="audio/wav",
        )

    headers["Content-Length"] = str(total_len)
    return StreamingResponse(
        iter([data]),
        status_code=status.HTTP_200_OK,
        headers=headers,
        media_type="audio/wav",
    )


@router.post("/{session_id}/export", response_model=ExportResponse)
async def export_session(
    session_id: str,
    payload: ExportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    session = await _load_full_session(session_id, current_user.id, db)
    return await export_session_content(session, payload.format)
