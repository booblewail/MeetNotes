from __future__ import annotations

import logging
import re
import time

from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import (
    serialize_action_item,
    serialize_block,
    serialize_section,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.genai_client import AFC_DISABLED, get_genai_client
from app.core.redis import broker
from app.models import (
    ActionItem,
    AgendaSection,
    NoteBlock,
    Session,
    SessionSpeaker,
    TranscriptSegment,
)
from app.schemas.llm_patches import Pass1PatchBatch, Pass1PatchOp
from app.schemas.ws_events import NotesPatchEvent

logger = logging.getLogger(__name__)


def _has_low_confidence(segments: list[TranscriptSegment]) -> bool:
    thresh = settings.LOW_CONFIDENCE_THRESHOLD
    for seg in segments:
        if seg.confidence < thresh:
            return True
        for w in seg.words_json or []:
            if float(w.get("confidence", 1.0)) < thresh:
                return True
    return False


def _extract_assignee(text: str) -> str | None:
    match = re.search(
        r"\b([A-Z][a-z]{1,20})\s+(?:will|to|should|can|needs to|owns)\b",
        text,
    )
    if match:
        candidate = match.group(1)
        if candidate.lower() not in {"we", "they", "this", "that", "it", "team", "let"}:
            return candidate
    return None


def _extract_due_date(text: str) -> str | None:
    match = re.search(
        r"\bby\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|tomorrow|next week|EOD|end of week|\d{4}-\d{2}-\d{2})\b",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _infer_topic_title(text: str, section_idx: int) -> str:
    lower = text.lower()
    keyword_topics = [
        (("auth", "oauth", "jwt", "login", "sso", "security"), "Authentication & Security"),
        (("database", "postgres", "pgvector", "schema", "migration"), "Database & Schema Design"),
        (("budget", "pricing", "cost", "spend", "revenue"), "Budget & Pricing"),
        (("launch", "release", "rollout", "timeline", "deadline", "roadmap"), "Release & Timeline"),
        (("mobile", "pwa", "ui", "design", "frontend", "workspace"), "Frontend & Mobile UX"),
        (("audio", "deepgram", "microphone", "vad", "transcript"), "Audio Capture & STT"),
        (("search", "embedding", "vector", "rag"), "Hybrid Search & Embeddings"),
    ]
    for keys, title in keyword_topics:
        if any(k in lower for k in keys):
            return title
    words = [w.strip(",.?!:;").capitalize() for w in text.split()[:4]]
    title = " ".join(w for w in words if w)
    return title or f"Discussion Topic {section_idx + 1}"


def _mock_pass1_patches(
    utterances: list[dict],
    existing_sections: list[AgendaSection],
) -> Pass1PatchBatch:
    """Deterministic zero-key Pass 1 patch generator for tests and local dev."""
    if not utterances:
        return Pass1PatchBatch(operations=[])

    ops: list[Pass1PatchOp] = []
    current_section_id = existing_sections[-1].id if existing_sections else None
    current_title = existing_sections[-1].title if existing_sections else None

    for utt in utterances:
        seg_id = str(utt["id"])
        text = str(utt["text"]).strip()
        start_ms = int(utt.get("start_ms", 0))
        if not text:
            continue

        inferred_title = _infer_topic_title(text, len(existing_sections))
        if current_section_id is None or (
            current_title != inferred_title
            and any(
                marker in text.lower()
                for marker in (
                    "let's move",
                    "next topic",
                    "switching to",
                    "regarding",
                    "about ",
                    "topic:",
                )
            )
        ):
            ops.append(
                Pass1PatchOp(
                    action="create_section",
                    section_title=inferred_title,
                    start_ms=start_ms,
                    source_segment_ids=[seg_id],
                )
            )
            current_title = inferred_title
            # Following ops in this batch without explicit section_id attach to newly created section
            current_section_id = "__NEW__"

        target_sec = None if current_section_id == "__NEW__" else current_section_id
        lower = text.lower()

        if any(
            k in lower
            for k in ("decided", "we will use", "agreed", "decision:", "let's go with", "finalized")
        ):
            ops.append(
                Pass1PatchOp(
                    action="add_decision",
                    section_id=target_sec,
                    content=text,
                    source_segment_ids=[seg_id],
                )
            )
        elif any(
            k in lower
            for k in ("action item", "todo", "will send", "will implement", "follow up", "needs to", "by friday", "by tomorrow")
        ):
            ops.append(
                Pass1PatchOp(
                    action="add_action_item",
                    section_id=target_sec,
                    content=text,
                    assignee=_extract_assignee(text),
                    due_date_text=_extract_due_date(text),
                    source_segment_ids=[seg_id],
                )
            )
        elif text.endswith("?") or any(
            k in lower for k in ("open question", "not sure if", "should we", "wonder if")
        ):
            ops.append(
                Pass1PatchOp(
                    action="add_open_question",
                    section_id=target_sec,
                    content=text,
                    source_segment_ids=[seg_id],
                )
            )
        else:
            ops.append(
                Pass1PatchOp(
                    action="append_bullet",
                    section_id=target_sec,
                    content=text,
                    source_segment_ids=[seg_id],
                )
            )

    return Pass1PatchBatch(operations=ops)


def build_thinking_config(
    model_name: str, budget: int, level: str | None = None
) -> types.ThinkingConfig | None:
    """Select a ThinkingConfig compatible with the target Gemini model family."""
    name = model_name.lower()
    if "flash-lite" in name:
        if budget == 0:
            return types.ThinkingConfig(thinking_level="MINIMAL")
        return types.ThinkingConfig(thinking_budget=budget)
    if level and name.startswith("gemini-4"):
        return types.ThinkingConfig(thinking_level=level)
    return types.ThinkingConfig(thinking_budget=budget)


def log_gemini_usage(stage: str, model_name: str, resp: object) -> None:
    """Log prompt, thinking, and output token counts reported by Gemini."""
    usage = getattr(resp, "usage_metadata", None)
    logger.info(
        "Gemini %s (%s) tokens: prompt=%s thoughts=%s output=%s total=%s",
        stage,
        model_name,
        getattr(usage, "prompt_token_count", None),
        getattr(usage, "thoughts_token_count", None),
        getattr(usage, "candidates_token_count", None),
        getattr(usage, "total_token_count", None),
    )


def _normalize_short_id(raw: str) -> str:
    """Strip whitespace, outer brackets [...], leading #, and quotes from a model ID token."""
    cleaned = str(raw).strip().strip("'\"").strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    return cleaned


def _resolve_prefixed_id(
    raw: str | None,
    prefix: str,
    ci_map: dict[str, str],
    valid_uuids: set[str],
) -> str | None:
    if not raw:
        return None
    clean = _normalize_short_id(raw)
    if not clean:
        return None
    upper = clean.upper()
    if upper in ci_map:
        return ci_map[upper]
    if clean.isdigit() and f"{prefix}{clean}" in ci_map:
        return ci_map[f"{prefix}{clean}"]
    if clean in valid_uuids:
        return clean
    return None


def coalesce_utterances(
    utterances: list[dict],
    max_gap_ms: int = 2500,
    max_words: int = 80,
) -> list[dict]:
    """Merge consecutive same-speaker utterances separated by a short pause."""
    turns: list[dict] = []
    for utt in utterances:
        text = str(utt.get("text") or "").strip()
        if not text:
            continue
        seg_id = str(utt["id"])
        speaker = str(utt.get("speaker_label") or "Speaker 1")
        start_ms = int(utt.get("start_ms", 0))
        end_ms = int(utt.get("end_ms", start_ms))
        words_len = len(text.split())

        if turns:
            prev = turns[-1]
            gap_ms = start_ms - prev["end_ms"]
            if (
                prev["speaker_label"] == speaker
                and gap_ms <= max_gap_ms
                and prev["_word_count"] + words_len <= max_words
            ):
                prev["segment_ids"].append(seg_id)
                prev["end_ms"] = max(prev["end_ms"], end_ms)
                prev["text"] = f"{prev['text']} {text}"
                prev["_word_count"] += words_len
                continue

        turns.append(
            {
                "segment_ids": [seg_id],
                "speaker_label": speaker,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "_word_count": words_len,
            }
        )

    for turn in turns:
        turn.pop("_word_count", None)
    return turns


def resolve_segment_ids(
    raw_ids: list[str],
    seg_id_map: dict[str, str | list[str]],
) -> list[str]:
    """Expand short integer keys (covering single UUIDs or coalesced UUID lists) to deduplicated DB UUIDs."""
    valid_seg_uuids: set[str] = set()
    for val in seg_id_map.values():
        if isinstance(val, list):
            valid_seg_uuids.update(val)
        else:
            valid_seg_uuids.add(val)

    mapped: list[str] = []
    for raw_sid in raw_ids:
        clean = _normalize_short_id(raw_sid)
        target = seg_id_map.get(clean)
        if isinstance(target, list):
            mapped.extend(target)
        elif isinstance(target, str):
            mapped.append(target)
        elif clean in valid_seg_uuids:
            mapped.append(clean)
    return list(dict.fromkeys(mapped))


def remap_pass1_batch_ids(
    batch: Pass1PatchBatch,
    seg_id_map: dict[str, str | list[str]],
    sec_id_map: dict[str, str],
) -> Pass1PatchBatch:
    """Map short integer segment/section IDs back to real database UUIDs."""
    valid_sec_uuids = set(sec_id_map.values())
    sec_ci = {k.upper(): v for k, v in sec_id_map.items()}

    remapped_ops: list[Pass1PatchOp] = []
    for op in batch.operations:
        deduped_segs = resolve_segment_ids(op.source_segment_ids, seg_id_map)
        if not deduped_segs:
            continue

        mapped_sec = _resolve_prefixed_id(
            op.section_id, "S", sec_ci, valid_sec_uuids
        )
        remapped_ops.append(
            op.model_copy(
                update={
                    "source_segment_ids": deduped_segs,
                    "section_id": mapped_sec,
                }
            )
        )

    return Pass1PatchBatch(operations=remapped_ops)


async def _call_gemini_pass1(
    session: Session,
    utterances: list[dict],
    existing_sections: list[AgendaSection],
) -> Pass1PatchBatch:
    if settings.is_mock_llm:
        return _mock_pass1_patches(utterances, existing_sections)

    recent_sections = existing_sections[-8:]
    sec_id_map = {
        f"S{idx}": s.id for idx, s in enumerate(recent_sections, start=1)
    }
    section_context = "\n".join(
        f"- id=S{idx} | title={s.title!r}"
        for idx, s in enumerate(recent_sections, start=1)
    ) or "(none yet — create an initial section)"

    turns = coalesce_utterances(utterances)
    if not turns:
        return Pass1PatchBatch(operations=[])

    seg_id_map: dict[str, str | list[str]] = {
        str(idx): turn["segment_ids"] for idx, turn in enumerate(turns, start=1)
    }
    utt_lines = "\n".join(
        f"[{idx}] {turn['speaker_label']}: {turn['text']}"
        for idx, turn in enumerate(turns, start=1)
    )
    pinned = "\n".join(
        f"- {m.get('offset_ms')}ms: {m.get('label') or 'Pinned moment'}"
        for m in (session.pinned_moments or [])
    )

    prompt = f"""You are MeetNotes Live Structurer (Pass 1).
Convert newly finalized transcript turns into atomic JSON note patches.

Rules:
1. Every operation MUST include non-empty source_segment_ids using ONLY the short integer IDs in brackets below (e.g. "1", "2").
2. Create a new section (`create_section`) only when a clearly new meeting topic starts or if no section exists yet; otherwise attach to an existing section using its short ID (e.g. "S1") or null.
3. Prefer concise bullets (`append_bullet`), clear choices (`add_decision`), concrete tasks (`add_action_item` with assignee/due_date_text if mentioned), and unresolved items (`add_open_question`).
4. Do not invent facts or segment IDs.

Existing Agenda Sections:
{section_context}

Pinned Moments (high-priority user markers):
{pinned or '(none)'}

New Utterances:
{utt_lines}
"""

    client = get_genai_client(timeout_ms=25_000)
    models_to_try = list(
        dict.fromkeys([settings.LLM_FAST_MODEL, "gemini-3.5-flash-lite", "gemini-3.6-flash"])
    )
    for model_name in models_to_try:
        thinking_cfg = build_thinking_config(model_name, settings.PASS1_THINKING_BUDGET)
        for cfg in (
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Pass1PatchBatch,
                temperature=0.2,
                max_output_tokens=1024,
                thinking_config=thinking_cfg,
                automatic_function_calling=AFC_DISABLED,
            ),
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Pass1PatchBatch,
                temperature=0.2,
                max_output_tokens=1024,
                automatic_function_calling=AFC_DISABLED,
            ),
        ):
            try:
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=cfg,
                )
                log_gemini_usage("pass1", model_name, resp)
                if resp.text:
                    parsed = Pass1PatchBatch.model_validate_json(resp.text)
                    return remap_pass1_batch_ids(parsed, seg_id_map, sec_id_map)
                break
            except Exception as exc:
                if (
                    cfg.thinking_config is not None
                    and "INVALID_ARGUMENT" in str(exc)
                ):
                    logger.debug(
                        "Retrying Pass 1 (%s) without thinking_config after INVALID_ARGUMENT",
                        model_name,
                    )
                    continue
                logger.warning("Gemini Pass 1 (%s) failed: %s", model_name, exc)
                break

    return _mock_pass1_patches(utterances, existing_sections)


async def apply_pass1_batch(
    db: AsyncSession,
    session: Session,
    batch: Pass1PatchBatch,
) -> NotesPatchEvent | None:
    """Validate citations, persist Pass 1 patches to DB, and return a NotesPatchEvent."""
    if not batch.operations:
        return None

    seg_res = await db.execute(
        select(TranscriptSegment).where(
            TranscriptSegment.session_id == session.id,
            TranscriptSegment.is_redacted.is_(False),
        )
    )
    valid_segments = {seg.id: seg for seg in seg_res.scalars().all()}

    sec_res = await db.execute(
        select(AgendaSection)
        .where(AgendaSection.session_id == session.id)
        .order_by(AgendaSection.order_index, AgendaSection.start_ms)
    )
    sections = list(sec_res.scalars().all())
    section_by_id = {s.id: s for s in sections}
    latest_section: AgendaSection | None = sections[-1] if sections else None

    max_sec_order = max((s.order_index for s in sections), default=-1)
    max_blk_order_res = await db.execute(
        select(func.coalesce(func.max(NoteBlock.order_index), -1)).where(
            NoteBlock.session_id == session.id
        )
    )
    next_blk_order = int(max_blk_order_res.scalar_one() or -1) + 1

    created_sections: list[AgendaSection] = []
    created_blocks: list[NoteBlock] = []
    created_actions: list[ActionItem] = []

    op_to_block_type = {
        "append_bullet": "bullet",
        "add_decision": "decision",
        "add_action_item": "action_item",
        "add_open_question": "open_question",
    }

    for op in batch.operations:
        cited_ids = [sid for sid in op.source_segment_ids if sid in valid_segments]
        if not cited_ids:
            continue
        cited_segs = [valid_segments[sid] for sid in cited_ids]

        if op.action == "create_section":
            max_sec_order += 1
            start_ms = (
                op.start_ms
                if op.start_ms is not None
                else min(s.start_ms for s in cited_segs)
            )
            section = AgendaSection(
                session_id=session.id,
                title=op.section_title or "Topic",
                start_ms=start_ms,
                order_index=max_sec_order,
                is_user_edited=False,
            )
            db.add(section)
            await db.flush()
            section_by_id[section.id] = section
            latest_section = section
            created_sections.append(section)
            continue

        target_section = (
            section_by_id.get(op.section_id) if op.section_id else None
        )
        if target_section is None:
            if latest_section is None:
                max_sec_order += 1
                latest_section = AgendaSection(
                    session_id=session.id,
                    title="General Discussion",
                    start_ms=min(s.start_ms for s in cited_segs),
                    order_index=max_sec_order,
                    is_user_edited=False,
                )
                db.add(latest_section)
                await db.flush()
                section_by_id[latest_section.id] = latest_section
                created_sections.append(latest_section)
            target_section = latest_section

        block_type = op_to_block_type[op.action]
        block = NoteBlock(
            session_id=session.id,
            section_id=target_section.id,
            block_type=block_type,
            content=op.content or "",
            assignee=op.assignee,
            due_date_text=op.due_date_text,
            completed=False,
            source_segment_ids=cited_ids,
            has_low_confidence=_has_low_confidence(cited_segs),
            is_user_edited=False,
            order_index=next_blk_order,
        )
        next_blk_order += 1
        db.add(block)
        await db.flush()
        created_blocks.append(block)

        if block_type == "action_item":
            action = ActionItem(
                session_id=session.id,
                block_id=block.id,
                description=block.content,
                assignee=block.assignee,
                due_date_text=block.due_date_text,
                completed=False,
                source_segment_ids=cited_ids,
            )
            db.add(action)
            await db.flush()
            created_actions.append(action)

    if not created_sections and not created_blocks:
        return None

    await db.commit()
    return NotesPatchEvent(
        session_id=session.id,
        created_sections=[serialize_section(s) for s in created_sections],
        created_blocks=[serialize_block(b) for b in created_blocks],
        created_action_items=[serialize_action_item(a) for a in created_actions],
    )


async def run_pass1_if_ready(session_id: str, force: bool = False) -> NotesPatchEvent | None:
    """Evaluate sliding-window thresholds (>=100 words or >=60s elapsed with >=25 words) and run Pass 1 safely."""
    pending = await broker.get_pending_utterances(session_id)
    if not pending:
        return None

    word_count = sum(len(str(u.get("text", "")).split()) for u in pending)
    now = time.monotonic()
    last_ts = await broker.get_last_pass1_ts(session_id)
    if last_ts is None:
        await broker.set_last_pass1_ts(session_id, now)
        last_ts = now
    elapsed = now - last_ts

    ready_by_words = word_count >= settings.PASS1_WORD_THRESHOLD
    ready_by_timer = (
        elapsed >= settings.PASS1_ELAPSED_SECONDS
        and word_count >= settings.PASS1_MIN_TIMER_WORDS
    )
    if not force and not (ready_by_words or ready_by_timer):
        return None

    acquired = await broker.acquire_pass1_lock(session_id)
    if not acquired:
        return None

    try:
        # Re-read pending inside lock so we capture exact slice count
        pending_locked = await broker.get_pending_utterances(session_id)
        if not pending_locked:
            return None

        async with AsyncSessionLocal() as db:
            sess_res = await db.execute(select(Session).where(Session.id == session_id))
            session = sess_res.scalar_one_or_none()
            if session is None:
                return None

            sec_res = await db.execute(
                select(AgendaSection)
                .where(AgendaSection.session_id == session_id)
                .order_by(AgendaSection.order_index, AgendaSection.start_ms)
            )
            existing_sections = list(sec_res.scalars().all())

            # Filter out utterances whose segment was already redacted
            seg_ids = [str(u["id"]) for u in pending_locked]
            active_res = await db.execute(
                select(TranscriptSegment.id).where(
                    TranscriptSegment.id.in_(seg_ids),
                    TranscriptSegment.is_redacted.is_(False),
                )
            )
            active_ids = set(active_res.scalars().all())
            active_utterances = [
                u for u in pending_locked if str(u["id"]) in active_ids
            ]

            patch_event: NotesPatchEvent | None = None
            if active_utterances:
                batch = await _call_gemini_pass1(
                    session, active_utterances, existing_sections
                )
                patch_event = await apply_pass1_batch(db, session, batch)

        await broker.pop_pending_utterances(session_id, len(pending_locked))
        await broker.set_last_pass1_ts(session_id, time.monotonic())
        if patch_event is not None:
            await broker.publish_session_event(session_id, patch_event.model_dump())
        return patch_event
    finally:
        await broker.release_pass1_lock(session_id)
