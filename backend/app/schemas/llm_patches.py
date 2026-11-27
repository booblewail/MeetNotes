from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator


# ==============================================================================
# Pass 1 Live Structuring Schema (Gemini Flash JSON Patch Output)
# Kept flat without dict[str, Any] so Gemini response_schema stays strictly valid.
# ==============================================================================

PatchActionType = Literal[
    "create_section",
    "append_bullet",
    "add_decision",
    "add_action_item",
    "add_open_question",
]


class Pass1PatchOp(BaseModel):
    """Single atomic live-notes patch emitted by Pass 1 structuring."""

    action: PatchActionType
    section_id: str | None = Field(
        default=None,
        description="Existing section short ID (e.g. 'S1') to attach this block to, or null for the latest/newly created section.",
    )
    section_title: str | None = Field(
        default=None,
        description="Required when action == 'create_section'; concise topic heading (2-6 words).",
    )
    start_ms: int | None = Field(
        default=None,
        ge=0,
        description="Meeting offset in milliseconds when this topic or point begins.",
    )
    content: str | None = Field(
        default=None,
        description="Concise note bullet, decision, action item, or open question text.",
    )
    assignee: str | None = Field(
        default=None,
        description="Optional owner name when action == 'add_action_item'.",
    )
    due_date_text: str | None = Field(
        default=None,
        description="Optional due date phrase (e.g. 'Friday', '2026-12-01') for action items.",
    )
    source_segment_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Non-empty list of short integer segment IDs (e.g. '1', '2') supporting this operation.",
    )

    @model_validator(mode="after")
    def validate_action_fields(self) -> Pass1PatchOp:
        if not self.source_segment_ids:
            raise ValueError("source_segment_ids must contain at least one segment ID")
        if self.action == "create_section":
            if not self.section_title or not self.section_title.strip():
                raise ValueError("create_section requires a non-empty section_title")
            self.section_title = self.section_title.strip()
        else:
            if not self.content or not self.content.strip():
                raise ValueError(f"{self.action} requires non-empty content")
            self.content = self.content.strip()
        return self


class Pass1PatchBatch(BaseModel):
    """Batch of atomic Pass 1 note patches returned by Gemini Flash."""

    operations: list[Pass1PatchOp] = Field(default_factory=list)


# ==============================================================================
# Pass 2 Full-Session Consolidation Schema (Gemini Pro Output)
# ==============================================================================

ConsolidatedBlockType = Literal[
    "bullet",
    "decision",
    "action_item",
    "open_question",
    "scratchpad",
]


class ConsolidatedBlockSpec(BaseModel):
    """Single consolidated note block in the finalized meeting document."""

    locked_block_id: str | None = Field(
        default=None,
        description="If preserving a human-locked block, MUST carry its exact short ID (e.g. 'L1').",
    )
    block_type: ConsolidatedBlockType
    content: str = Field(..., min_length=1)
    assignee: str | None = None
    due_date_text: str | None = None
    source_segment_ids: list[str] = Field(
        default_factory=list,
        description="Short integer segment IDs (e.g. '1', '2') supporting this block.",
    )


class ConsolidatedSectionSpec(BaseModel):
    """Topic section in the consolidated meeting outline."""

    existing_section_id: str | None = Field(
        default=None,
        description="Existing section short ID (e.g. 'S1') if preserving or merging into a live section.",
    )
    title: str = Field(..., min_length=1)
    start_ms: int = Field(default=0, ge=0)
    blocks: list[ConsolidatedBlockSpec] = Field(default_factory=list)


class Pass2ConsolidationOutput(BaseModel):
    """Strict structured output contract for Pass 2 post-meeting synthesis."""

    title_suggestion: str | None = Field(
        default=None,
        description="Refined descriptive meeting title when the current title is generic.",
    )
    executive_summary: str = Field(
        ...,
        min_length=1,
        description="3-5 sentence executive summary covering key outcomes, decisions, and next steps.",
    )
    sections: list[ConsolidatedSectionSpec] = Field(default_factory=list)
