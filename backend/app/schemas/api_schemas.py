from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.ws_events import (
    ActionItemPayload,
    AgendaSectionPayload,
    NoteBlockPayload,
    PinnedMomentPayload,
    SpeakerPayload,
    TranscriptSegmentPayload,
)

AudioRetentionPolicy = Literal["delete_after_processing", "retain_7d", "retain_30d"]
SessionStatus = Literal[
    "created", "recording", "paused", "processing", "completed", "failed"
]


# ==============================================================================
# Auth & User Schemas
# ==============================================================================


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=120)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    default_audio_retention: AudioRetentionPolicy | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    default_audio_retention: AudioRetentionPolicy
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ==============================================================================
# Session Schemas
# ==============================================================================


class SessionCreateRequest(BaseModel):
    title: str = Field(default="Untitled Meeting", min_length=1, max_length=255)
    audio_retention: AudioRetentionPolicy | None = None


class SessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    executive_summary: str | None = None
    audio_retention: AudioRetentionPolicy | None = None


class SessionSummaryResponse(BaseModel):
    id: str
    title: str
    status: SessionStatus
    executive_summary: str | None = None
    duration_ms: int
    audio_retention: AudioRetentionPolicy
    audio_available: bool = False
    agenda_preview: list[str] = Field(default_factory=list)
    decision_count: int = 0
    action_item_count: int = 0
    created_at: datetime
    updated_at: datetime


class SessionDetailResponse(BaseModel):
    id: str
    user_id: str
    title: str
    status: SessionStatus
    executive_summary: str | None = None
    duration_ms: int
    audio_retention: AudioRetentionPolicy
    audio_available: bool = False
    audio_expires_at: datetime | None = None
    pinned_moments: list[PinnedMomentPayload] = Field(default_factory=list)
    speakers: list[SpeakerPayload] = Field(default_factory=list)
    segments: list[TranscriptSegmentPayload] = Field(default_factory=list)
    sections: list[AgendaSectionPayload] = Field(default_factory=list)
    blocks: list[NoteBlockPayload] = Field(default_factory=list)
    action_items: list[ActionItemPayload] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PinMomentRequest(BaseModel):
    offset_ms: int = Field(..., ge=0)
    label: str | None = Field(default=None, max_length=240)


class RedactWindowRequest(BaseModel):
    window_ms: int = Field(default=120_000, ge=1_000, le=3_600_000)
    current_offset_ms: int | None = Field(default=None, ge=0)


class SpeakerRenameRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)


class AudioUrlResponse(BaseModel):
    url: str
    stream_url: str
    expires_at: datetime | None = None


# ==============================================================================
# Blocks, Sections & Action Items
# ==============================================================================


class BlockCreateRequest(BaseModel):
    section_id: str | None = None
    block_type: Literal[
        "bullet", "decision", "action_item", "open_question", "scratchpad"
    ] = "scratchpad"
    content: str = Field(..., min_length=1, max_length=4000)
    assignee: str | None = Field(default=None, max_length=120)
    due_date_text: str | None = Field(default=None, max_length=120)
    source_segment_ids: list[str] = Field(default_factory=list)


class BlockUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    block_type: Literal[
        "bullet", "decision", "action_item", "open_question", "scratchpad"
    ] | None = None
    section_id: str | None = None
    assignee: str | None = Field(default=None, max_length=120)
    due_date_text: str | None = Field(default=None, max_length=120)
    completed: bool | None = None


class SectionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class ActionItemUpdateRequest(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    assignee: str | None = Field(default=None, max_length=120)
    due_date_text: str | None = Field(default=None, max_length=120)
    completed: bool | None = None


# ==============================================================================
# Glossary, Export & Hybrid Search
# ==============================================================================


class GlossaryTermCreateRequest(BaseModel):
    term: str = Field(..., min_length=1, max_length=120)
    boost: float = Field(default=1.5, ge=0.5, le=5.0)


class GlossaryTermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    term: str
    boost: float
    created_at: datetime


class ExportRequest(BaseModel):
    format: Literal["markdown", "slack_recap", "followup_email"] = "markdown"


class ExportResponse(BaseModel):
    format: Literal["markdown", "slack_recap", "followup_email"]
    filename: str
    content: str


class SearchHitResponse(BaseModel):
    kind: Literal["section", "session"]
    session_id: str
    session_title: str
    section_id: str | None = None
    section_title: str | None = None
    start_ms: int = 0
    snippet: str
    score: float
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    created_at: datetime


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitResponse] = Field(default_factory=list)
