from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import EmbeddingVector, JSONVariant


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


SPEAKER_COLORS = [
    "#3B82F6",  # Blue
    "#10B981",  # Emerald
    "#8B5CF6",  # Violet
    "#F59E0B",  # Amber
    "#EC4899",  # Pink
    "#06B6D4",  # Cyan
    "#EF4444",  # Red
    "#14B8A6",  # Teal
]


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    default_audio_retention: Mapped[str] = mapped_column(
        String(32), nullable=False, default="retain_7d"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    glossary_terms: Mapped[list[GlossaryTerm]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, default="Untitled Meeting"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audio_s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audio_retention: Mapped[str] = mapped_column(
        String(32), nullable=False, default="retain_7d"
    )
    audio_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned_moments: Mapped[list[dict]] = mapped_column(
        JSONVariant, nullable=False, default=list
    )
    summary_embedding: Mapped[list[float] | None] = mapped_column(
        EmbeddingVector(768), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="sessions")
    speakers: Mapped[list[SessionSpeaker]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionSpeaker.diarization_index",
    )
    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="TranscriptSegment.start_ms",
    )
    sections: Mapped[list[AgendaSection]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AgendaSection.order_index",
    )
    blocks: Mapped[list[NoteBlock]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="NoteBlock.order_index",
    )
    action_items: Mapped[list[ActionItem]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ActionItem.created_at",
    )


class SessionSpeaker(Base):
    __tablename__ = "session_speakers"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "diarization_index", name="uq_session_speaker_idx"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    diarization_index: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    color_hex: Mapped[str] = mapped_column(String(16), nullable=False, default="#3B82F6")

    session: Mapped[Session] = relationship(back_populates="speakers")
    segments: Mapped[list[TranscriptSegment]] = relationship(back_populates="speaker")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    speaker_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("session_speakers.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    words_json: Mapped[list[dict]] = mapped_column(
        JSONVariant, nullable=False, default=list
    )
    is_redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    session: Mapped[Session] = relationship(back_populates="segments")
    speaker: Mapped[SessionSpeaker | None] = relationship(back_populates="segments")


class AgendaSection(Base):
    __tablename__ = "agenda_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_user_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        EmbeddingVector(768), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    session: Mapped[Session] = relationship(back_populates="sections")
    blocks: Mapped[list[NoteBlock]] = relationship(
        back_populates="section", order_by="NoteBlock.order_index"
    )


class NoteBlock(Base):
    __tablename__ = "note_blocks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    section_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agenda_sections.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    block_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="bullet"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(120), nullable=True)
    due_date_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_segment_ids: Mapped[list[str]] = mapped_column(
        JSONVariant, nullable=False, default=list
    )
    has_low_confidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_user_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    session: Mapped[Session] = relationship(back_populates="blocks")
    section: Mapped[AgendaSection | None] = relationship(back_populates="blocks")
    action_items: Mapped[list[ActionItem]] = relationship(
        back_populates="block", cascade="all, delete-orphan"
    )


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    block_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("note_blocks.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(120), nullable=True)
    due_date_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_segment_ids: Mapped[list[str]] = mapped_column(
        JSONVariant, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    session: Mapped[Session] = relationship(back_populates="action_items")
    block: Mapped[NoteBlock | None] = relationship(back_populates="action_items")


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    __table_args__ = (
        UniqueConstraint("user_id", "term", name="uq_user_glossary_term"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    term: Mapped[str] = mapped_column(String(120), nullable=False)
    boost: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="glossary_terms")
