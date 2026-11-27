"""Initial MeetNotes schema with pgvector and full-text indexes

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-24 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.types import EmbeddingVector, JSONVariant

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column(
            "default_audio_retention",
            sa.String(length=32),
            nullable=False,
            server_default="retain_7d",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_s3_key", sa.String(length=512), nullable=True),
        sa.Column(
            "audio_retention",
            sa.String(length=32),
            nullable=False,
            server_default="retain_7d",
        ),
        sa.Column("audio_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pinned_moments", JSONVariant, nullable=False),
        sa.Column("summary_embedding", EmbeddingVector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "session_speakers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("diarization_index", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("color_hex", sa.String(length=16), nullable=False),
        sa.UniqueConstraint(
            "session_id", "diarization_index", name="uq_session_speaker_idx"
        ),
    )
    op.create_index("ix_session_speakers_session_id", "session_speakers", ["session_id"])

    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "speaker_id",
            sa.String(length=36),
            sa.ForeignKey("session_speakers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("words_json", JSONVariant, nullable=False),
        sa.Column(
            "is_redacted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_transcript_segments_session_id", "transcript_segments", ["session_id"]
    )
    op.create_index(
        "ix_transcript_segments_speaker_id", "transcript_segments", ["speaker_id"]
    )
    op.create_index(
        "ix_transcript_segments_start_ms", "transcript_segments", ["start_ms"]
    )

    op.create_table(
        "agenda_sections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_user_edited", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("embedding", EmbeddingVector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agenda_sections_session_id", "agenda_sections", ["session_id"])

    op.create_table(
        "note_blocks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.String(length=36),
            sa.ForeignKey("agenda_sections.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("block_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("assignee", sa.String(length=120), nullable=True),
        sa.Column("due_date_text", sa.String(length=120), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_segment_ids", JSONVariant, nullable=False),
        sa.Column(
            "has_low_confidence",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_user_edited", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_note_blocks_session_id", "note_blocks", ["session_id"])
    op.create_index("ix_note_blocks_section_id", "note_blocks", ["section_id"])

    op.create_table(
        "action_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_id",
            sa.String(length=36),
            sa.ForeignKey("note_blocks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("assignee", sa.String(length=120), nullable=True),
        sa.Column("due_date_text", sa.String(length=120), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_segment_ids", JSONVariant, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_action_items_session_id", "action_items", ["session_id"])
    op.create_index("ix_action_items_block_id", "action_items", ["block_id"])

    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term", sa.String(length=120), nullable=False),
        sa.Column("boost", sa.Float(), nullable=False, server_default="1.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "term", name="uq_user_glossary_term"),
    )
    op.create_index("ix_glossary_terms_user_id", "glossary_terms", ["user_id"])

    if is_pg:
        op.execute(
            "CREATE INDEX ix_sessions_fts ON sessions "
            "USING GIN (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(executive_summary, '')))"
        )
        op.execute(
            "CREATE INDEX ix_agenda_sections_fts ON agenda_sections "
            "USING GIN (to_tsvector('english', coalesce(title, '')))"
        )
        op.execute(
            "CREATE INDEX ix_note_blocks_fts ON note_blocks "
            "USING GIN (to_tsvector('english', coalesce(content, '')))"
        )
        op.execute(
            "CREATE INDEX ix_sessions_summary_hnsw ON sessions "
            "USING hnsw (summary_embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_agenda_sections_hnsw ON agenda_sections "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.drop_table("glossary_terms")
    op.drop_table("action_items")
    op.drop_table("note_blocks")
    op.drop_table("agenda_sections")
    op.drop_table("transcript_segments")
    op.drop_table("session_speakers")
    op.drop_table("sessions")
    op.drop_table("users")
