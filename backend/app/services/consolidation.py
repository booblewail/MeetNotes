from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    TranscriptSegment,
)
from app.schemas.api_schemas import ExportResponse
from app.schemas.llm_patches import (
    ConsolidatedBlockSpec,
    ConsolidatedSectionSpec,
    Pass2ConsolidationOutput,
)
from app.schemas.ws_events import (
    SessionConsolidatedEvent,
    SessionRedactedEvent,
    SessionStatusEvent,
)
from app.services.embeddings import embed_texts
from app.services.storage import audio_storage, format_ms
from app.services.structuring import (
    _has_low_confidence,
    _infer_topic_title,
    _resolve_prefixed_id,
    build_thinking_config,
    coalesce_utterances,
    log_gemini_usage,
    resolve_segment_ids,
)

logger = logging.getLogger(__name__)

_EXPORT_CACHE_MAX = 128
_EXPORT_CACHE: OrderedDict[tuple[str, str, str], str] = OrderedDict()


def clear_export_cache() -> None:
    """Clear the bounded AI export cache."""
    _EXPORT_CACHE.clear()


def _mock_pass2_consolidation(
    session: Session,
    segments: list[TranscriptSegment],
    sections: list[AgendaSection],
    blocks: list[NoteBlock],
) -> Pass2ConsolidationOutput:
    """Deterministic Pass 2 synthesis for zero-key dev and test runs."""
    locked_blocks = [b for b in blocks if b.is_user_edited]
    unedited_blocks = [b for b in blocks if not b.is_user_edited]

    # Group unedited blocks by section title (merging duplicate titles)
    merged_sections: dict[str, ConsolidatedSectionSpec] = {}
    section_id_to_key: dict[str, str] = {}

    for sec in sorted(sections, key=lambda s: (s.order_index, s.start_ms)):
        norm = sec.title.strip().lower()
        if norm not in merged_sections:
            merged_sections[norm] = ConsolidatedSectionSpec(
                existing_section_id=sec.id,
                title=sec.title,
                start_ms=sec.start_ms,
                blocks=[],
            )
        section_id_to_key[sec.id] = norm

    # Ensure at least one section exists if segments exist without Pass 1 sections
    if not merged_sections and segments:
        first_title = _infer_topic_title(segments[0].text, 0)
        key = first_title.lower()
        merged_sections[key] = ConsolidatedSectionSpec(
            existing_section_id=None,
            title=first_title,
            start_ms=segments[0].start_ms,
            blocks=[],
        )

    # Place locked blocks first in their target sections so Pass 2 preserves placement
    for lb in locked_blocks:
        target_key = section_id_to_key.get(lb.section_id or "")
        if target_key is None:
            if not merged_sections:
                merged_sections["notes"] = ConsolidatedSectionSpec(
                    existing_section_id=None,
                    title="Meeting Notes",
                    start_ms=0,
                    blocks=[],
                )
            target_key = next(iter(merged_sections.keys()))
        merged_sections[target_key].blocks.append(
            ConsolidatedBlockSpec(
                locked_block_id=lb.id,
                block_type=lb.block_type,  # type: ignore[arg-type]
                content=lb.content,
                assignee=lb.assignee,
                due_date_text=lb.due_date_text,
                source_segment_ids=list(lb.source_segment_ids or []),
            )
        )

    # Deduplicate unedited blocks by normalized content
    seen_contents: set[str] = {
        re.sub(r"\s+", " ", lb.content.strip().lower()) for lb in locked_blocks
    }
    for ub in unedited_blocks:
        norm_text = re.sub(r"\s+", " ", ub.content.strip().lower())
        if norm_text in seen_contents:
            continue
        seen_contents.add(norm_text)
        target_key = section_id_to_key.get(ub.section_id or "")
        if target_key is None:
            target_key = next(iter(merged_sections.keys()))
        merged_sections[target_key].blocks.append(
            ConsolidatedBlockSpec(
                locked_block_id=None,
                block_type=ub.block_type,  # type: ignore[arg-type]
                content=ub.content,
                assignee=ub.assignee,
                due_date_text=ub.due_date_text,
                source_segment_ids=list(ub.source_segment_ids or []),
            )
        )

    # If no blocks existed yet (e.g., short meeting stopped before Pass 1 threshold), summarize segments
    if segments and all(len(s.blocks) == 0 for s in merged_sections.values()):
        target = next(iter(merged_sections.values()))
        for seg in segments:
            lower = seg.text.lower()
            btype: Literal["bullet", "decision", "action_item", "open_question"] = "bullet"
            if any(k in lower for k in ("decided", "agreed", "decision", "we will use")):
                btype = "decision"
            elif any(k in lower for k in ("action", "todo", "will send", "will implement", "by friday")):
                btype = "action_item"
            elif seg.text.strip().endswith("?"):
                btype = "open_question"
            target.blocks.append(
                ConsolidatedBlockSpec(
                    locked_block_id=None,
                    block_type=btype,
                    content=seg.text.strip(),
                    source_segment_ids=[seg.id],
                )
            )

    sec_list = sorted(merged_sections.values(), key=lambda s: s.start_ms)
    decisions = [
        b.content for s in sec_list for b in s.blocks if b.block_type == "decision"
    ]
    actions = [
        b.content for s in sec_list for b in s.blocks if b.block_type == "action_item"
    ]
    topic_names = ", ".join(s.title for s in sec_list[:4]) or "key topics"
    summary_parts = [f"The team discussed {topic_names}."]
    if decisions:
        summary_parts.append(f"Key decisions: {'; '.join(decisions[:2])}.")
    if actions:
        summary_parts.append(f"Action items captured: {len(actions)} follow-up(s) assigned.")
    if not decisions and not actions and segments:
        summary_parts.append(f"Captured {len(segments)} transcript segments.")

    title_suggestion = None
    if session.title.strip().lower() in ("untitled meeting", "new meeting", "") and sec_list:
        title_suggestion = sec_list[0].title

    return Pass2ConsolidationOutput(
        title_suggestion=title_suggestion,
        executive_summary=" ".join(summary_parts),
        sections=sec_list,
    )


def remap_pass2_output_ids(
    output: Pass2ConsolidationOutput,
    seg_id_map: dict[str, str | list[str]],
    sec_id_map: dict[str, str],
    locked_id_map: dict[str, str],
) -> Pass2ConsolidationOutput:
    """Map short segment, section, and locked-block IDs back to real database UUIDs."""
    valid_sec_uuids = set(sec_id_map.values())
    valid_locked_uuids = set(locked_id_map.values())
    sec_ci = {k.upper(): v for k, v in sec_id_map.items()}
    locked_ci = {k.upper(): v for k, v in locked_id_map.items()}

    remapped_sections: list[ConsolidatedSectionSpec] = []
    for sec in output.sections:
        mapped_sec_id = _resolve_prefixed_id(
            sec.existing_section_id, "S", sec_ci, valid_sec_uuids
        )
        remapped_blocks: list[ConsolidatedBlockSpec] = []
        for blk in sec.blocks:
            mapped_locked_id = _resolve_prefixed_id(
                blk.locked_block_id, "L", locked_ci, valid_locked_uuids
            )
            mapped_cites = resolve_segment_ids(blk.source_segment_ids, seg_id_map)
            remapped_blocks.append(
                blk.model_copy(
                    update={
                        "locked_block_id": mapped_locked_id,
                        "source_segment_ids": mapped_cites,
                    }
                )
            )
        remapped_sections.append(
            sec.model_copy(
                update={
                    "existing_section_id": mapped_sec_id,
                    "blocks": remapped_blocks,
                }
            )
        )

    return output.model_copy(update={"sections": remapped_sections})


async def _call_gemini_pass2(
    session: Session,
    segments: list[TranscriptSegment],
    sections: list[AgendaSection],
    blocks: list[NoteBlock],
) -> Pass2ConsolidationOutput:
    if settings.is_mock_llm:
        return _mock_pass2_consolidation(session, segments, sections, blocks)

    speaker_map = {sp.id: sp.display_name for sp in session.speakers}
    turns = coalesce_utterances(
        [
            {
                "id": seg.id,
                "speaker_label": speaker_map.get(seg.speaker_id or "", "Speaker 1"),
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "text": seg.text,
            }
            for seg in segments
        ]
    )
    seg_id_map: dict[str, str | list[str]] = {
        str(idx): turn["segment_ids"] for idx, turn in enumerate(turns, start=1)
    }
    transcript_text = "\n".join(
        f"[{idx}] ({turn['start_ms']}ms) {turn['speaker_label']}: {turn['text']}"
        for idx, turn in enumerate(turns, start=1)
    )
    pinned_text = "\n".join(
        f"- {format_ms(int(m.get('offset_ms', 0)))} ({m.get('offset_ms')}ms): {m.get('label') or 'Pinned moment'}"
        for m in (session.pinned_moments or [])
    )
    sec_id_map = {f"S{idx}": s.id for idx, s in enumerate(sections, start=1)}
    sec_uuid_to_short = {v: k for k, v in sec_id_map.items()}
    locked_blocks = [b for b in blocks if b.is_user_edited]
    locked_id_map = {f"L{idx}": b.id for idx, b in enumerate(locked_blocks, start=1)}

    locked_text = "\n".join(
        f"- locked_block_id=L{idx} | section_id={sec_uuid_to_short.get(b.section_id or '', 'none')} | type={b.block_type} | content={b.content!r}"
        for idx, b in enumerate(locked_blocks, start=1)
    )
    existing_sections_text = "\n".join(
        f"- existing_section_id=S{idx} | start_ms={s.start_ms} | title={s.title!r}"
        for idx, s in enumerate(sections, start=1)
    )

    prompt = f"""You are MeetNotes Pass 2 Consolidation Synthesizer.
Produce the final polished meeting document from the full transcript, pinned moments, and human-locked skeleton notes.

CRITICAL RULES:
1. HUMAN LOCK PRESERVATION: Every locked block listed below MUST appear in the output with `locked_block_id` set to its exact short ID (e.g. "L1") and its `content` preserved verbatim. You may add `source_segment_ids` that corroborate it.
2. MERGE & DEDUPLICATE: Merge fragmented topics into clean chronological sections with accurate `start_ms` values and `existing_section_id` short ID (e.g. "S1") when applicable.
3. CITATIONS: Every AI-generated block MUST include valid `source_segment_ids` using ONLY the short integer IDs in brackets below (e.g. "1", "2").
4. EXECUTIVE SUMMARY: Write a crisp 3-5 sentence executive summary highlighting decisions, rationale, and next steps.

Current Title: {session.title}

Existing Sections:
{existing_sections_text or '(none)'}

Human-Locked Blocks (MUST PRESERVE VERBATIM):
{locked_text or '(none)'}

Pinned Moments (high-priority highlights):
{pinned_text or '(none)'}

Full Transcript:
{transcript_text or '(empty transcript)'}
"""

    client = get_genai_client(timeout_ms=45_000)
    models_to_try = list(
        dict.fromkeys(
            [settings.LLM_CONSOLIDATION_MODEL, settings.LLM_FAST_MODEL, "gemini-3.6-flash"]
        )
    )
    for model_name in models_to_try:
        thinking_cfg = build_thinking_config(
            model_name,
            settings.PASS2_THINKING_BUDGET,
            level=settings.PASS2_THINKING_LEVEL,
        )
        for cfg in (
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Pass2ConsolidationOutput,
                temperature=0.2,
                max_output_tokens=4096,
                thinking_config=thinking_cfg,
                automatic_function_calling=AFC_DISABLED,
            ),
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Pass2ConsolidationOutput,
                temperature=0.2,
                max_output_tokens=4096,
                automatic_function_calling=AFC_DISABLED,
            ),
        ):
            try:
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=cfg,
                )
                log_gemini_usage("pass2", model_name, resp)
                if resp.text:
                    parsed = Pass2ConsolidationOutput.model_validate_json(resp.text)
                    return remap_pass2_output_ids(
                        parsed, seg_id_map, sec_id_map, locked_id_map
                    )
                break
            except Exception as exc:
                if (
                    cfg.thinking_config is not None
                    and "INVALID_ARGUMENT" in str(exc)
                ):
                    logger.debug(
                        "Retrying Pass 2 (%s) without thinking_config after INVALID_ARGUMENT",
                        model_name,
                    )
                    continue
                logger.warning("Gemini Pass 2 (%s) failed: %s", model_name, exc)
                break

    return _mock_pass2_consolidation(session, segments, sections, blocks)


async def consolidate_session(session_id: str) -> SessionConsolidatedEvent | None:
    """Execute Pass 2 consolidation, enforce user-edit locks, persist embeddings & audio."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Session)
            .where(Session.id == session_id)
            .options(
                selectinload(Session.speakers),
                selectinload(Session.segments),
                selectinload(Session.sections),
                selectinload(Session.blocks),
                selectinload(Session.action_items),
            )
        )
        session = res.scalar_one_or_none()
        if session is None:
            return None

        active_segments = [
            seg
            for seg in sorted(session.segments, key=lambda s: s.start_ms)
            if not seg.is_redacted
        ]
        segment_by_id = {seg.id: seg for seg in active_segments}

        existing_sections = list(session.sections)
        existing_blocks = list(session.blocks)
        locked_blocks_by_id = {
            b.id: b for b in existing_blocks if b.is_user_edited
        }
        locked_sections_by_id = {
            s.id: s for s in existing_sections if s.is_user_edited
        }

        output = await _call_gemini_pass2(
            session, active_segments, existing_sections, existing_blocks
        )

        if (
            output.title_suggestion
            and session.title.strip().lower() in ("untitled meeting", "new meeting", "")
        ):
            session.title = output.title_suggestion.strip()[:255]
        session.executive_summary = output.executive_summary.strip()

        # Delete ONLY unedited blocks and unedited action items; keep user-edited items intact
        for ai in list(session.action_items):
            if ai.block_id and ai.block_id in locked_blocks_by_id:
                continue
            await db.delete(ai)

        for blk in list(existing_blocks):
            if not blk.is_user_edited:
                await db.delete(blk)
        await db.flush()

        # Prepare sections: reuse existing_section_id when valid, or create new sections
        existing_sec_by_id = {s.id: s for s in existing_sections}
        kept_section_ids: set[str] = set()
        final_sections: list[AgendaSection] = []
        seen_locked_block_ids: set[str] = set()
        global_blk_order = 0

        for sec_idx, sec_spec in enumerate(output.sections):
            sec_obj: AgendaSection | None = None
            if sec_spec.existing_section_id and sec_spec.existing_section_id in existing_sec_by_id:
                cand = existing_sec_by_id[sec_spec.existing_section_id]
                if cand.id not in kept_section_ids:
                    sec_obj = cand
                    if not cand.is_user_edited:
                        cand.title = sec_spec.title.strip()[:255]
                        cand.start_ms = sec_spec.start_ms
                    cand.order_index = sec_idx

            if sec_obj is None:
                sec_obj = AgendaSection(
                    session_id=session.id,
                    title=sec_spec.title.strip()[:255],
                    start_ms=sec_spec.start_ms,
                    order_index=sec_idx,
                    is_user_edited=False,
                )
                db.add(sec_obj)
                await db.flush()

            kept_section_ids.add(sec_obj.id)
            final_sections.append(sec_obj)

            for blk_spec in sec_spec.blocks:
                valid_cites = [
                    sid for sid in blk_spec.source_segment_ids if sid in segment_by_id
                ]
                cited_segs = [segment_by_id[sid] for sid in valid_cites]

                if (
                    blk_spec.locked_block_id
                    and blk_spec.locked_block_id in locked_blocks_by_id
                ):
                    lb = locked_blocks_by_id[blk_spec.locked_block_id]
                    if lb.id in seen_locked_block_ids:
                        continue
                    seen_locked_block_ids.add(lb.id)
                    # Hard Lock Enforcement: NEVER overwrite user content/type/assignee/completed;
                    # only attach section placement & merge valid citations.
                    lb.section_id = sec_obj.id
                    lb.order_index = global_blk_order
                    lb.is_user_edited = True
                    existing_cites = [
                        sid for sid in (lb.source_segment_ids or []) if sid in segment_by_id
                    ]
                    merged_cites = list(dict.fromkeys([*existing_cites, *valid_cites]))
                    lb.source_segment_ids = merged_cites
                    if merged_cites:
                        lb.has_low_confidence = _has_low_confidence(
                            [segment_by_id[sid] for sid in merged_cites]
                        )
                    global_blk_order += 1
                    continue

                new_blk = NoteBlock(
                    session_id=session.id,
                    section_id=sec_obj.id,
                    block_type=blk_spec.block_type,
                    content=blk_spec.content.strip(),
                    assignee=blk_spec.assignee,
                    due_date_text=blk_spec.due_date_text,
                    completed=False,
                    source_segment_ids=valid_cites,
                    has_low_confidence=_has_low_confidence(cited_segs),
                    is_user_edited=False,
                    order_index=global_blk_order,
                )
                global_blk_order += 1
                db.add(new_blk)
                await db.flush()

                if new_blk.block_type == "action_item":
                    db.add(
                        ActionItem(
                            session_id=session.id,
                            block_id=new_blk.id,
                            description=new_blk.content,
                            assignee=new_blk.assignee,
                            due_date_text=new_blk.due_date_text,
                            completed=False,
                            source_segment_ids=valid_cites,
                        )
                    )

        # Preserve any locked sections that LLM omitted
        for ls_id, ls_obj in locked_sections_by_id.items():
            if ls_id not in kept_section_ids:
                ls_obj.order_index = len(final_sections)
                kept_section_ids.add(ls_id)
                final_sections.append(ls_obj)

        # Safety net: guarantee NO locked block can ever be orphaned or dropped by Pass 2
        missing_locked = [
            lb for lb_id, lb in locked_blocks_by_id.items() if lb_id not in seen_locked_block_ids
        ]
        if missing_locked:
            fallback_section: AgendaSection | None = None
            for lb in missing_locked:
                if lb.section_id and lb.section_id in kept_section_ids:
                    lb.order_index = global_blk_order
                    lb.is_user_edited = True
                    global_blk_order += 1
                    continue
                if lb.section_id and lb.section_id in existing_sec_by_id:
                    restored = existing_sec_by_id[lb.section_id]
                    restored.order_index = len(final_sections)
                    kept_section_ids.add(restored.id)
                    final_sections.append(restored)
                    lb.order_index = global_blk_order
                    lb.is_user_edited = True
                    global_blk_order += 1
                    continue
                if fallback_section is None:
                    if final_sections:
                        fallback_section = final_sections[0]
                    else:
                        fallback_section = AgendaSection(
                            session_id=session.id,
                            title="Pinned & Manual Notes",
                            start_ms=0,
                            order_index=0,
                            is_user_edited=True,
                        )
                        db.add(fallback_section)
                        await db.flush()
                        kept_section_ids.add(fallback_section.id)
                        final_sections.append(fallback_section)
                lb.section_id = fallback_section.id
                lb.order_index = global_blk_order
                lb.is_user_edited = True
                global_blk_order += 1

        # Remove leftover unreferenced, unedited sections
        for old_sec in existing_sections:
            if old_sec.id not in kept_section_ids and not old_sec.is_user_edited:
                await db.delete(old_sec)

        await db.flush()

        # Reload current sections and blocks to generate embeddings
        sec_res = await db.execute(
            select(AgendaSection)
            .where(AgendaSection.session_id == session.id)
            .order_by(AgendaSection.order_index, AgendaSection.start_ms)
        )
        all_sections = list(sec_res.scalars().all())
        blk_res = await db.execute(
            select(NoteBlock)
            .where(NoteBlock.session_id == session.id)
            .order_by(NoteBlock.order_index, NoteBlock.created_at)
        )
        all_blocks = list(blk_res.scalars().all())

        texts_to_embed = [f"{session.title}\n{session.executive_summary or ''}"]
        for sec in all_sections:
            sec_blocks = [b.content for b in all_blocks if b.section_id == sec.id]
            texts_to_embed.append(f"{sec.title}\n" + "\n".join(sec_blocks))

        vectors = await embed_texts(texts_to_embed)
        if vectors:
            session.summary_embedding = vectors[0]
            for sec, vec in zip(all_sections, vectors[1:]):
                sec.embedding = vec

        await persist_session_audio_from_spool(db, session)

        session.status = "completed"
        await db.commit()

        # Reload action items for final event payload
        ai_res = await db.execute(
            select(ActionItem)
            .where(ActionItem.session_id == session.id)
            .order_by(ActionItem.created_at)
        )
        all_actions = list(ai_res.scalars().all())

        event = SessionConsolidatedEvent(
            session_id=session.id,
            title=session.title,
            status=session.status,
            executive_summary=session.executive_summary or "",
            sections=[serialize_section(s) for s in all_sections],
            blocks=[serialize_block(b) for b in all_blocks],
            action_items=[serialize_action_item(a) for a in all_actions],
            audio_available=bool(session.audio_s3_key),
        )
        await broker.clear_pending_utterances(session.id)
        await broker.publish_session_event(
            session.id,
            SessionStatusEvent(
                session_id=session.id,
                status="completed",
                duration_ms=session.duration_ms,
            ).model_dump(),
        )
        await broker.publish_session_event(session.id, event.model_dump())
        return event


async def persist_session_audio_from_spool(
    db: AsyncSession, session: Session
) -> None:
    """Idempotently enforce audio retention and upload spooled WAV to S3/local fallback."""
    now = datetime.now(UTC)
    if session.audio_retention == "delete_after_processing":
        if session.audio_s3_key:
            await audio_storage.delete_object(session.audio_s3_key)
        audio_storage.delete_spool_file(session.id)
        session.audio_s3_key = None
        session.audio_expires_at = None
        return

    wav_bytes = audio_storage.build_wav_bytes(session.id)
    if wav_bytes:
        s3_key = await audio_storage.upload_wav_bytes(session.id, wav_bytes)
        session.audio_s3_key = s3_key
        audio_storage.delete_spool_file(session.id)

    if session.audio_s3_key and session.audio_expires_at is None:
        days = 30 if session.audio_retention == "retain_30d" else 7
        session.audio_expires_at = now + timedelta(days=days)


async def enqueue_or_run_consolidation(
    session_id: str, run_inline: bool = False
) -> None:
    if run_inline:
        await consolidate_session(session_id)
        return
    arq_pool = await broker.get_arq_pool()
    if arq_pool is not None:
        try:
            await arq_pool.enqueue_job("finalize_session_task", session_id)
            return
        except Exception as exc:
            logger.warning("ARQ enqueue failed (%s); running inline background task", exc)
    asyncio.create_task(consolidate_session(session_id))


async def redact_session_window(
    db: AsyncSession,
    session_id: str,
    window_ms: int = 120_000,
    current_offset_ms: int | None = None,
) -> SessionRedactedEvent:
    """Redact the last `window_ms` of transcript segments, audio spool/S3, and unedited blocks."""
    sess_res = await db.execute(select(Session).where(Session.id == session_id))
    session = sess_res.scalar_one()

    seg_res = await db.execute(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.session_id == session_id,
            TranscriptSegment.is_redacted.is_(False),
        )
        .order_by(TranscriptSegment.start_ms)
    )
    active_segs = list(seg_res.scalars().all())
    max_seg_end = max((s.end_ms for s in active_segs), default=0)
    to_ms = max(
        current_offset_ms if current_offset_ms is not None else 0,
        session.duration_ms,
        max_seg_end,
    )
    from_ms = max(0, to_ms - window_ms)

    redacted_ids: list[str] = []
    for seg in active_segs:
        if seg.end_ms >= from_ms and seg.start_ms <= to_ms:
            seg.is_redacted = True
            seg.text = "[Redacted]"
            seg.words_json = []
            redacted_ids.append(seg.id)

    redacted_set = set(redacted_ids)

    # Redact audio spool and any stored WAV
    audio_storage.redact_spool_window(session_id, from_ms, to_ms)
    if session.audio_s3_key:
        wav_bytes = await audio_storage.get_audio_bytes(
            session.audio_s3_key, session_id
        )
        if wav_bytes:
            clean_wav = audio_storage.redact_wav_bytes(wav_bytes, from_ms, to_ms)
            await audio_storage.upload_wav_bytes(session_id, clean_wav)

    # Remove unedited blocks whose citations all fell within the redacted window
    deleted_block_ids: list[str] = []
    if redacted_set:
        blk_res = await db.execute(
            select(NoteBlock).where(NoteBlock.session_id == session_id)
        )
        for blk in blk_res.scalars().all():
            cites = list(blk.source_segment_ids or [])
            if not cites:
                continue
            remaining = [sid for sid in cites if sid not in redacted_set]
            if not remaining and not blk.is_user_edited:
                deleted_block_ids.append(blk.id)
                await db.delete(blk)
            elif len(remaining) != len(cites):
                blk.source_segment_ids = remaining

    await db.commit()
    return SessionRedactedEvent(
        session_id=session_id,
        from_ms=from_ms,
        to_ms=to_ms,
        redacted_segment_ids=redacted_ids,
        deleted_block_ids=deleted_block_ids,
    )


def format_session_markdown(session: Session) -> str:
    lines: list[str] = [
        f"# {session.title}",
        "",
        f"- **Date:** {session.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **Duration:** {format_ms(session.duration_ms)}",
        f"- **Status:** {session.status}",
        "",
    ]
    if session.executive_summary:
        lines.extend(["## Executive Summary", "", session.executive_summary, ""])

    if session.pinned_moments:
        lines.append("## Pinned Moments")
        lines.append("")
        for m in session.pinned_moments:
            ts = format_ms(int(m.get("offset_ms", 0)))
            lbl = m.get("label") or "Key moment"
            lines.append(f"- **[{ts}]** {lbl}")
        lines.append("")

    sections = sorted(session.sections, key=lambda s: (s.order_index, s.start_ms))
    blocks = sorted(session.blocks, key=lambda b: (b.order_index, b.created_at))

    for sec in sections:
        lines.append(f"## {sec.title} ({format_ms(sec.start_ms)})")
        lines.append("")
        sec_blocks = [b for b in blocks if b.section_id == sec.id]
        for b in sec_blocks:
            lock_tag = " [Locked]" if b.is_user_edited else ""
            if b.block_type == "decision":
                lines.append(f"- **Decision:** {b.content}{lock_tag}")
            elif b.block_type == "action_item":
                mark = "x" if b.completed else " "
                meta = []
                if b.assignee:
                    meta.append(f"@{b.assignee}")
                if b.due_date_text:
                    meta.append(f"due {b.due_date_text}")
                suffix = f" ({', '.join(meta)})" if meta else ""
                lines.append(f"- [{mark}] {b.content}{suffix}{lock_tag}")
            elif b.block_type == "open_question":
                lines.append(f"- **Open Question:** {b.content}{lock_tag}")
            else:
                lines.append(f"- {b.content}{lock_tag}")
        lines.append("")

    unassigned = [b for b in blocks if not b.section_id]
    if unassigned:
        lines.append("## Scratchpad")
        lines.append("")
        for b in unassigned:
            lines.append(f"- {b.content}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _mock_export_transform(
    session: Session, fmt: Literal["slack_recap", "followup_email"]
) -> str:
    blocks = list(session.blocks)
    decisions = [b.content for b in blocks if b.block_type == "decision"]
    actions = [b for b in blocks if b.block_type == "action_item"]
    bullets = [b.content for b in blocks if b.block_type == "bullet"]

    if fmt == "slack_recap":
        lines = [f"*Meeting Recap: {session.title}* ({format_ms(session.duration_ms)})"]
        if session.executive_summary:
            lines.append(f"• *Summary:* {session.executive_summary}")
        for d in decisions[:2]:
            lines.append(f"• *Decision:* {d}")
        for a in actions[:2]:
            owner = f" (@{a.assignee})" if a.assignee else ""
            lines.append(f"• *Action:* {a.content}{owner}")
        for b in bullets[:max(0, 5 - len(lines))]:
            lines.append(f"• {b}")
        return "\n".join(lines[:6])

    # followup_email
    lines = [
        f"Subject: Follow-up & Recap — {session.title}",
        "",
        "Hi team,",
        "",
        session.executive_summary or f"Thanks for joining today's discussion on {session.title}.",
        "",
    ]
    if decisions:
        lines.append("Key Decisions:")
        for d in decisions:
            lines.append(f"- {d}")
        lines.append("")
    if actions:
        lines.append("Next Steps:")
        for a in actions:
            owner = f" [{a.assignee}]" if a.assignee else ""
            due = f" (Due: {a.due_date_text})" if a.due_date_text else ""
            lines.append(f"- {a.content}{owner}{due}")
        lines.append("")
    lines.extend(["Best,", "MeetNotes Recap"])
    return "\n".join(lines)


async def export_session_content(
    session: Session,
    fmt: Literal["markdown", "slack_recap", "followup_email"],
) -> ExportResponse:
    slug = re.sub(r"[^a-z0-9]+", "-", session.title.lower()).strip("-") or "meeting"
    md = format_session_markdown(session)
    if fmt == "markdown":
        return ExportResponse(format=fmt, filename=f"{slug}.md", content=md)

    if settings.is_mock_llm:
        return ExportResponse(
            format=fmt,
            filename=f"{slug}-{fmt}.txt",
            content=_mock_export_transform(session, fmt),
        )

    cache_key = (
        session.id,
        fmt,
        hashlib.sha256(md.encode("utf-8")).hexdigest(),
    )
    cached_content = _EXPORT_CACHE.get(cache_key)
    if cached_content is not None:
        _EXPORT_CACHE.move_to_end(cache_key)
        return ExportResponse(
            format=fmt,
            filename=f"{slug}-{fmt}.txt",
            content=cached_content,
        )

    instruction = (
        "Convert these meeting notes into a crisp 5-bullet Slack update using Slack mrkdwn."
        if fmt == "slack_recap"
        else "Convert these meeting notes into a concise, polite follow-up email with Subject line, decisions, and next steps."
    )
    try:
        client = get_genai_client(timeout_ms=30_000)
        thinking_cfg = build_thinking_config(
            settings.LLM_FAST_MODEL, settings.PASS1_THINKING_BUDGET
        )
        for cfg in (
            types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1024,
                thinking_config=thinking_cfg,
                automatic_function_calling=AFC_DISABLED,
            ),
            types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1024,
                automatic_function_calling=AFC_DISABLED,
            ),
        ):
            try:
                resp = await client.aio.models.generate_content(
                    model=settings.LLM_FAST_MODEL,
                    contents=f"{instruction}\n\n{md}",
                    config=cfg,
                )
                log_gemini_usage(f"export:{fmt}", settings.LLM_FAST_MODEL, resp)
                if resp.text:
                    rendered = resp.text.strip()
                    _EXPORT_CACHE[cache_key] = rendered
                    if len(_EXPORT_CACHE) > _EXPORT_CACHE_MAX:
                        _EXPORT_CACHE.popitem(last=False)
                    return ExportResponse(
                        format=fmt,
                        filename=f"{slug}-{fmt}.txt",
                        content=rendered,
                    )
                break
            except Exception as exc:
                if (
                    cfg.thinking_config is not None
                    and "INVALID_ARGUMENT" in str(exc)
                ):
                    continue
                raise
    except Exception as exc:
        logger.warning("Gemini export transform failed (%s); using template", exc)

    return ExportResponse(
        format=fmt,
        filename=f"{slug}-{fmt}.txt",
        content=_mock_export_transform(session, fmt),
    )
