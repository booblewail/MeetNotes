from __future__ import annotations

from app.core.config import settings
from app.models import (
    ActionItem,
    AgendaSection,
    NoteBlock,
    Session,
    SessionSpeaker,
    TranscriptSegment,
)
from app.schemas.api_schemas import SessionDetailResponse, SessionSummaryResponse
from app.schemas.ws_events import (
    ActionItemPayload,
    AgendaSectionPayload,
    NoteBlockPayload,
    PinnedMomentPayload,
    SpeakerPayload,
    TranscriptSegmentPayload,
    WordTokenPayload,
)


def serialize_speaker(speaker: SessionSpeaker) -> SpeakerPayload:
    return SpeakerPayload(
        id=speaker.id,
        session_id=speaker.session_id,
        diarization_index=speaker.diarization_index,
        display_name=speaker.display_name,
        color_hex=speaker.color_hex,
    )


def serialize_segment(
    segment: TranscriptSegment,
    speaker_map: dict[str, SessionSpeaker] | None = None,
) -> TranscriptSegmentPayload:
    speaker = None
    if speaker_map and segment.speaker_id:
        speaker = speaker_map.get(segment.speaker_id)
    elif segment.speaker_id and hasattr(segment, "speaker"):
        speaker = segment.speaker
    speaker_label = speaker.display_name if speaker else "Speaker 1"
    speaker_color = speaker.color_hex if speaker else "#3B82F6"
    words = [WordTokenPayload(**w) for w in (segment.words_json or [])]
    has_low = segment.confidence < settings.LOW_CONFIDENCE_THRESHOLD or any(
        w.confidence < settings.LOW_CONFIDENCE_THRESHOLD for w in words
    )
    return TranscriptSegmentPayload(
        id=segment.id,
        session_id=segment.session_id,
        speaker_id=segment.speaker_id,
        speaker_label=speaker_label,
        speaker_color=speaker_color,
        text="[Redacted]" if segment.is_redacted else segment.text,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        confidence=segment.confidence,
        has_low_confidence=has_low,
        is_redacted=segment.is_redacted,
        words=[] if segment.is_redacted else words,
    )


def serialize_section(section: AgendaSection) -> AgendaSectionPayload:
    return AgendaSectionPayload(
        id=section.id,
        session_id=section.session_id,
        title=section.title,
        start_ms=section.start_ms,
        order_index=section.order_index,
        is_user_edited=section.is_user_edited,
    )


def serialize_block(block: NoteBlock) -> NoteBlockPayload:
    return NoteBlockPayload(
        id=block.id,
        session_id=block.session_id,
        section_id=block.section_id,
        block_type=block.block_type,  # type: ignore[arg-type]
        content=block.content,
        assignee=block.assignee,
        due_date_text=block.due_date_text,
        completed=block.completed,
        source_segment_ids=list(block.source_segment_ids or []),
        has_low_confidence=block.has_low_confidence,
        is_user_edited=block.is_user_edited,
        order_index=block.order_index,
    )


def serialize_action_item(item: ActionItem) -> ActionItemPayload:
    return ActionItemPayload(
        id=item.id,
        session_id=item.session_id,
        block_id=item.block_id,
        description=item.description,
        assignee=item.assignee,
        due_date_text=item.due_date_text,
        completed=item.completed,
        source_segment_ids=list(item.source_segment_ids or []),
    )


def serialize_session_summary(session: Session) -> SessionSummaryResponse:
    sections = sorted(session.sections, key=lambda s: (s.order_index, s.start_ms))
    blocks = list(session.blocks)
    return SessionSummaryResponse(
        id=session.id,
        title=session.title,
        status=session.status,  # type: ignore[arg-type]
        executive_summary=session.executive_summary,
        duration_ms=session.duration_ms,
        audio_retention=session.audio_retention,  # type: ignore[arg-type]
        audio_available=bool(session.audio_s3_key),
        agenda_preview=[sec.title for sec in sections[:4]],
        decision_count=sum(1 for b in blocks if b.block_type == "decision"),
        action_item_count=len(session.action_items),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def serialize_session_detail(session: Session) -> SessionDetailResponse:
    speaker_map = {sp.id: sp for sp in session.speakers}
    visible_segments = [
        serialize_segment(seg, speaker_map)
        for seg in sorted(session.segments, key=lambda s: s.start_ms)
        if not seg.is_redacted
    ]
    sections = sorted(session.sections, key=lambda s: (s.order_index, s.start_ms))
    blocks = sorted(session.blocks, key=lambda b: (b.order_index, b.created_at))
    moments = [PinnedMomentPayload(**m) for m in (session.pinned_moments or [])]
    return SessionDetailResponse(
        id=session.id,
        user_id=session.user_id,
        title=session.title,
        status=session.status,  # type: ignore[arg-type]
        executive_summary=session.executive_summary,
        duration_ms=session.duration_ms,
        audio_retention=session.audio_retention,  # type: ignore[arg-type]
        audio_available=bool(session.audio_s3_key),
        audio_expires_at=session.audio_expires_at,
        pinned_moments=moments,
        speakers=[serialize_speaker(sp) for sp in session.speakers],
        segments=visible_segments,
        sections=[serialize_section(sec) for sec in sections],
        blocks=[serialize_block(b) for b in blocks],
        action_items=[serialize_action_item(ai) for ai in session.action_items],
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
