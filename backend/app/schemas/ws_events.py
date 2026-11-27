from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field


# ==============================================================================
# Client -> Server Control Frames (JSON text frames on /ws/sessions/{id})
# ==============================================================================


class PauseMessage(BaseModel):
    type: Literal["pause"]


class ResumeMessage(BaseModel):
    type: Literal["resume"]


class PinMomentMessage(BaseModel):
    type: Literal["pin_moment"]
    offset_ms: int = Field(..., ge=0)
    label: str | None = Field(default=None, max_length=240)


class RedactWindowMessage(BaseModel):
    type: Literal["redact_window"]
    window_ms: int = Field(default=120_000, ge=1_000, le=3_600_000)
    current_offset_ms: int | None = Field(default=None, ge=0)


class StopSessionMessage(BaseModel):
    type: Literal["stop_session"]


class SyncStateMessage(BaseModel):
    type: Literal["sync_state"]


class SimulateUtteranceMessage(BaseModel):
    """Test & zero-key demo control frame to inject a live utterance into the pipeline."""

    type: Literal["simulate_utterance"]
    speaker_label: str = Field(default="Speaker 1", min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=4000)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.96, ge=0.0, le=1.0)
    force_pass1: bool = False


ClientWSMessage = Annotated[
    Union[
        PauseMessage,
        ResumeMessage,
        PinMomentMessage,
        RedactWindowMessage,
        StopSessionMessage,
        SyncStateMessage,
        SimulateUtteranceMessage,
    ],
    Field(discriminator="type"),
]


# ==============================================================================
# Shared Payload Submodels for Server -> Client Events
# ==============================================================================


class WordTokenPayload(BaseModel):
    word: str
    start_ms: int
    end_ms: int
    confidence: float
    speaker: int | None = None


class TranscriptSegmentPayload(BaseModel):
    id: str
    session_id: str
    speaker_id: str | None = None
    speaker_label: str
    speaker_color: str = "#3B82F6"
    text: str
    start_ms: int
    end_ms: int
    confidence: float
    has_low_confidence: bool = False
    is_redacted: bool = False
    words: list[WordTokenPayload] = Field(default_factory=list)


class AgendaSectionPayload(BaseModel):
    id: str
    session_id: str
    title: str
    start_ms: int
    order_index: int
    is_user_edited: bool = False


class NoteBlockPayload(BaseModel):
    id: str
    session_id: str
    section_id: str | None = None
    block_type: Literal[
        "bullet", "decision", "action_item", "open_question", "scratchpad"
    ]
    content: str
    assignee: str | None = None
    due_date_text: str | None = None
    completed: bool = False
    source_segment_ids: list[str] = Field(default_factory=list)
    has_low_confidence: bool = False
    is_user_edited: bool = False
    order_index: int = 0


class ActionItemPayload(BaseModel):
    id: str
    session_id: str
    block_id: str | None = None
    description: str
    assignee: str | None = None
    due_date_text: str | None = None
    completed: bool = False
    source_segment_ids: list[str] = Field(default_factory=list)


class PinnedMomentPayload(BaseModel):
    offset_ms: int
    label: str | None = None
    created_at: str


class SpeakerPayload(BaseModel):
    id: str
    session_id: str
    diarization_index: int
    display_name: str
    color_hex: str


# ==============================================================================
# Server -> Client Events (Relayed over Redis Pub/Sub -> /ws/sessions/{id})
# ==============================================================================


class SessionReadyEvent(BaseModel):
    type: Literal["session.ready"] = "session.ready"
    session_id: str
    status: str
    stt_mode: Literal["deepgram", "mock"]
    llm_mode: Literal["gemini", "mock"]


class TranscriptInterimEvent(BaseModel):
    type: Literal["transcript.interim"] = "transcript.interim"
    session_id: str
    speaker_label: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float = 0.9


class TranscriptFinalEvent(BaseModel):
    type: Literal["transcript.final"] = "transcript.final"
    session_id: str
    segment: TranscriptSegmentPayload
    speaker: SpeakerPayload


class MomentPinnedEvent(BaseModel):
    type: Literal["moment.pinned"] = "moment.pinned"
    session_id: str
    moment: PinnedMomentPayload


class NotesPatchEvent(BaseModel):
    type: Literal["notes.patch"] = "notes.patch"
    session_id: str
    created_sections: list[AgendaSectionPayload] = Field(default_factory=list)
    created_blocks: list[NoteBlockPayload] = Field(default_factory=list)
    created_action_items: list[ActionItemPayload] = Field(default_factory=list)


class BlockUpdatedEvent(BaseModel):
    type: Literal["block.updated"] = "block.updated"
    session_id: str
    action: Literal["created", "updated", "deleted"]
    block: NoteBlockPayload | None = None
    block_id: str


class SectionUpdatedEvent(BaseModel):
    type: Literal["section.updated"] = "section.updated"
    session_id: str
    section: AgendaSectionPayload


class SpeakerUpdatedEvent(BaseModel):
    type: Literal["speaker.updated"] = "speaker.updated"
    session_id: str
    speaker: SpeakerPayload


class SessionRedactedEvent(BaseModel):
    type: Literal["session.redacted"] = "session.redacted"
    session_id: str
    from_ms: int
    to_ms: int
    redacted_segment_ids: list[str]
    deleted_block_ids: list[str]


class SessionStatusEvent(BaseModel):
    type: Literal["session.status"] = "session.status"
    session_id: str
    status: Literal[
        "created", "recording", "paused", "processing", "completed", "failed"
    ]
    duration_ms: int = 0


class SessionConsolidatedEvent(BaseModel):
    type: Literal["session.consolidated"] = "session.consolidated"
    session_id: str
    title: str
    status: str
    executive_summary: str
    sections: list[AgendaSectionPayload]
    blocks: list[NoteBlockPayload]
    action_items: list[ActionItemPayload]
    audio_available: bool = False


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


ServerWSEvent = Union[
    SessionReadyEvent,
    TranscriptInterimEvent,
    TranscriptFinalEvent,
    MomentPinnedEvent,
    NotesPatchEvent,
    BlockUpdatedEvent,
    SectionUpdatedEvent,
    SpeakerUpdatedEvent,
    SessionRedactedEvent,
    SessionStatusEvent,
    SessionConsolidatedEvent,
    ErrorEvent,
]
