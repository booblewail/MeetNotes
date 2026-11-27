from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
import struct
from array import array
from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_ws
from app.api.serializers import serialize_segment, serialize_speaker
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import broker
from app.models import (
    SPEAKER_COLORS,
    GlossaryTerm,
    Session,
    SessionSpeaker,
    TranscriptSegment,
)
from app.schemas.ws_events import (
    ClientWSMessage,
    ErrorEvent,
    MomentPinnedEvent,
    PinnedMomentPayload,
    SessionReadyEvent,
    SessionStatusEvent,
    TranscriptFinalEvent,
    TranscriptInterimEvent,
)
from app.services.consolidation import (
    enqueue_or_run_consolidation,
    persist_session_audio_from_spool,
    redact_session_window,
)
from app.services.storage import audio_storage
from app.services.stt_stream import (
    MockLiveStream,
    STTTranscriptEvent,
    create_stt_stream,
)
from app.services.structuring import run_pass1_if_ready

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ws"])

_client_msg_adapter = TypeAdapter(ClientWSMessage)


async def _get_or_create_speaker(
    db: AsyncSession, session_id: str, diarization_index: int
) -> SessionSpeaker:
    res = await db.execute(
        select(SessionSpeaker).where(
            SessionSpeaker.session_id == session_id,
            SessionSpeaker.diarization_index == diarization_index,
        )
    )
    speaker = res.scalar_one_or_none()
    if speaker is not None:
        return speaker
    color = SPEAKER_COLORS[diarization_index % len(SPEAKER_COLORS)]
    speaker = SessionSpeaker(
        session_id=session_id,
        diarization_index=diarization_index,
        display_name=f"Speaker {diarization_index + 1}",
        color_hex=color,
    )
    db.add(speaker)
    await db.flush()
    return speaker


def _parse_speaker_index(label: str) -> int:
    match = re.search(r"(\d+)", label)
    if match:
        val = int(match.group(1))
        return max(0, val - 1)
    return 0


async def _persist_final_stt(
    session_id: str, stt_evt: STTTranscriptEvent, custom_speaker_name: str | None = None
) -> None:
    async with AsyncSessionLocal() as db:
        sess_res = await db.execute(select(Session).where(Session.id == session_id))
        session = sess_res.scalar_one_or_none()
        if session is None:
            return

        speaker = await _get_or_create_speaker(db, session_id, stt_evt.speaker_index)
        if custom_speaker_name and speaker.display_name.startswith("Speaker "):
            speaker.display_name = custom_speaker_name

        seg = TranscriptSegment(
            session_id=session_id,
            speaker_id=speaker.id,
            text=stt_evt.text,
            start_ms=stt_evt.start_ms,
            end_ms=stt_evt.end_ms,
            confidence=stt_evt.confidence,
            words_json=[
                {
                    "word": w.word,
                    "start_ms": w.start_ms,
                    "end_ms": w.end_ms,
                    "confidence": w.confidence,
                    "speaker": w.speaker,
                }
                for w in stt_evt.words
            ],
            is_redacted=False,
        )
        db.add(seg)
        session.duration_ms = max(session.duration_ms, stt_evt.end_ms)
        await db.commit()
        await db.refresh(seg)
        await db.refresh(speaker)

        seg_dto = serialize_segment(seg, {speaker.id: speaker})
        speaker_dto = serialize_speaker(speaker)

    await broker.publish_session_event(
        session_id,
        TranscriptFinalEvent(
            session_id=session_id,
            segment=seg_dto,
            speaker=speaker_dto,
        ).model_dump(),
    )
    await broker.push_pending_utterance(
        session_id,
        {
            "id": seg_dto.id,
            "speaker_label": seg_dto.speaker_label,
            "text": seg_dto.text,
            "start_ms": seg_dto.start_ms,
            "end_ms": seg_dto.end_ms,
            "confidence": seg_dto.confidence,
        },
    )


@router.websocket("/ws/sessions/{session_id}")
async def session_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
) -> None:
    auth_token = token
    if not auth_token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            auth_token = auth_header.removeprefix("Bearer ").strip()

    async with AsyncSessionLocal() as db:
        user = await get_current_user_ws(auth_token, db)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        sess_res = await db.execute(
            select(Session).where(
                Session.id == session_id, Session.user_id == user.id
            )
        )
        session = sess_res.scalar_one_or_none()
        if session is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        terms_res = await db.execute(
            select(GlossaryTerm.term).where(GlossaryTerm.user_id == user.id)
        )
        keyterms = [row for row in terms_res.scalars().all()]

        if session.status in ("created", "paused"):
            session.status = "recording"
            await db.commit()

        current_status = session.status
        current_duration = session.duration_ms

    await websocket.accept()

    stt_stream: DeepgramLiveStream | MockLiveStream | None = None
    stt_task: asyncio.Task | None = None

    ready_evt = SessionReadyEvent(
        session_id=session_id,
        status=current_status,
        stt_mode="mock" if settings.is_mock_stt else "deepgram",
        llm_mode="mock" if settings.is_mock_llm else "gemini",
    )
    await websocket.send_text(ready_evt.model_dump_json())

    # 1. Pub/Sub -> WebSocket forwarder
    async def _pubsub_forwarder() -> None:
        async for event in broker.subscribe_session_events(session_id):
            await websocket.send_text(json.dumps(event))

    # 2. STT Event consumer -> DB + Pub/Sub + Pass 1 trigger
    async def _stt_consumer(stream: DeepgramLiveStream | MockLiveStream) -> None:
        while True:
            evt = await stream.next_event()
            if evt is None:
                break
            if not evt.is_final:
                await broker.publish_session_event(
                    session_id,
                    TranscriptInterimEvent(
                        session_id=session_id,
                        speaker_label=f"Speaker {evt.speaker_index + 1}",
                        text=evt.text,
                        start_ms=evt.start_ms,
                        end_ms=evt.end_ms,
                        confidence=evt.confidence,
                    ).model_dump(),
                )
                continue

            await _persist_final_stt(session_id, evt)
            asyncio.create_task(run_pass1_if_ready(session_id, force=False))

    async def _ensure_stt_started() -> DeepgramLiveStream | MockLiveStream:
        nonlocal stt_stream, stt_task
        if stt_stream is not None:
            return stt_stream
        stream = create_stt_stream(keyterms=keyterms)
        try:
            await stream.start()
        except Exception as exc:
            logger.warning(
                "Failed to open Deepgram live stream (%s); falling back to mock", exc
            )
            stream = MockLiveStream(keyterms=keyterms)
            await stream.start()
        stt_stream = stream
        stt_task = asyncio.create_task(_stt_consumer(stream))
        return stream

    async def _close_stt() -> None:
        nonlocal stt_stream, stt_task
        stream = stt_stream
        task = stt_task
        stt_stream = None
        stt_task = None
        if stream is not None:
            await stream.close()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    pubsub_task = asyncio.create_task(_pubsub_forwarder())
    latest_offset_ms = current_duration
    is_paused = current_status == "paused"

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                raw_bytes: bytes = message["bytes"]
                if len(raw_bytes) < 4:
                    continue
                offset_ms = struct.unpack("<I", raw_bytes[:4])[0]
                pcm_chunk = raw_bytes[4:]
                if is_paused or not pcm_chunk:
                    continue
                latest_offset_ms = max(latest_offset_ms, offset_ms)
                audio_storage.append_pcm_chunk(session_id, offset_ms, pcm_chunk)
                stream = await _ensure_stt_started()
                await stream.send_audio(offset_ms, pcm_chunk)
                continue

            text_data = message.get("text")
            if not text_data:
                continue

            try:
                ctrl = _client_msg_adapter.validate_json(text_data)
            except ValidationError as exc:
                await websocket.send_text(
                    ErrorEvent(
                        code="invalid_message",
                        message=str(exc),
                    ).model_dump_json()
                )
                continue

            if ctrl.type == "pause":
                is_paused = True
                await _close_stt()
                async with AsyncSessionLocal() as db:
                    res = await db.execute(select(Session).where(Session.id == session_id))
                    s = res.scalar_one_or_none()
                    if s and s.status == "recording":
                        s.status = "paused"
                        s.duration_ms = max(s.duration_ms, latest_offset_ms)
                        await db.commit()
                        await broker.publish_session_event(
                            session_id,
                            SessionStatusEvent(
                                session_id=session_id,
                                status="paused",
                                duration_ms=s.duration_ms,
                            ).model_dump(),
                        )

            elif ctrl.type == "resume":
                is_paused = False
                async with AsyncSessionLocal() as db:
                    res = await db.execute(select(Session).where(Session.id == session_id))
                    s = res.scalar_one_or_none()
                    if s and s.status in ("paused", "created"):
                        s.status = "recording"
                        await db.commit()
                        await broker.publish_session_event(
                            session_id,
                            SessionStatusEvent(
                                session_id=session_id,
                                status="recording",
                                duration_ms=s.duration_ms,
                            ).model_dump(),
                        )

            elif ctrl.type == "pin_moment":
                moment = PinnedMomentPayload(
                    offset_ms=ctrl.offset_ms,
                    label=ctrl.label,
                    created_at=datetime.now(UTC).isoformat(),
                )
                async with AsyncSessionLocal() as db:
                    res = await db.execute(select(Session).where(Session.id == session_id))
                    s = res.scalar_one_or_none()
                    if s:
                        moments = list(s.pinned_moments or [])
                        moments.append(moment.model_dump())
                        s.pinned_moments = moments
                        await db.commit()
                await broker.publish_session_event(
                    session_id,
                    MomentPinnedEvent(session_id=session_id, moment=moment).model_dump(),
                )

            elif ctrl.type == "redact_window":
                async with AsyncSessionLocal() as db:
                    evt = await redact_session_window(
                        db=db,
                        session_id=session_id,
                        window_ms=ctrl.window_ms,
                        current_offset_ms=ctrl.current_offset_ms or latest_offset_ms,
                    )
                await broker.publish_session_event(session_id, evt.model_dump())

            elif ctrl.type == "simulate_utterance":
                sp_idx = _parse_speaker_index(ctrl.speaker_label)
                start_ms = (
                    ctrl.start_ms if ctrl.start_ms is not None else latest_offset_ms
                )
                end_ms = (
                    ctrl.end_ms
                    if ctrl.end_ms is not None
                    else start_ms + max(1500, len(ctrl.text.split()) * 350)
                )
                latest_offset_ms = max(latest_offset_ms, end_ms)

                words_raw = ctrl.text.split()
                step = max(50, (end_ms - start_ms) // max(1, len(words_raw)))
                from app.services.stt_stream import STTWord

                stt_evt = STTTranscriptEvent(
                    text=ctrl.text.strip(),
                    speaker_index=sp_idx,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=ctrl.confidence,
                    words=[
                        STTWord(
                            word=w,
                            start_ms=start_ms + i * step,
                            end_ms=start_ms + (i + 1) * step,
                            confidence=ctrl.confidence,
                            speaker=sp_idx,
                        )
                        for i, w in enumerate(words_raw)
                    ],
                    is_final=True,
                )
                custom_name = (
                    ctrl.speaker_label
                    if not ctrl.speaker_label.lower().startswith("speaker ")
                    else None
                )
                tone_ms = min(3000, max(250, end_ms - start_ms))
                num_samples = (16000 * tone_ms) // 1000
                freq = 220 + (sp_idx * 110)
                pcm_samples = array(
                    "h",
                    (
                        int(2500 * math.sin(2.0 * math.pi * freq * (i / 16000.0)))
                        for i in range(num_samples)
                    ),
                )
                audio_storage.append_pcm_chunk(session_id, start_ms, pcm_samples.tobytes())
                await _persist_final_stt(session_id, stt_evt, custom_speaker_name=custom_name)
                await run_pass1_if_ready(session_id, force=ctrl.force_pass1)

            elif ctrl.type == "stop_session":
                await _close_stt()
                async with AsyncSessionLocal() as db:
                    res = await db.execute(select(Session).where(Session.id == session_id))
                    s = res.scalar_one_or_none()
                    if s:
                        s.status = "processing"
                        s.duration_ms = max(s.duration_ms, latest_offset_ms)
                        await persist_session_audio_from_spool(db, s)
                        await db.commit()
                        dur = s.duration_ms
                    else:
                        dur = latest_offset_ms
                await broker.publish_session_event(
                    session_id,
                    SessionStatusEvent(
                        session_id=session_id,
                        status="processing",
                        duration_ms=dur,
                    ).model_dump(),
                )
                await enqueue_or_run_consolidation(session_id, run_inline=False)

            elif ctrl.type == "sync_state":
                await websocket.send_text(
                    SessionStatusEvent(
                        session_id=session_id,
                        status="paused" if is_paused else "recording",
                        duration_ms=latest_offset_ms,
                    ).model_dump_json()
                )

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for session %s", session_id)
    finally:
        pubsub_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pubsub_task
        await _close_stt()
