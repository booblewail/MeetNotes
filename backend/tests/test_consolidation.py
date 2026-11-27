from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
import pytest
from sqlalchemy import select

from app.core import database
from app.models import AgendaSection, NoteBlock, Session, TranscriptSegment
from app.schemas.llm_patches import (
    ConsolidatedBlockSpec,
    ConsolidatedSectionSpec,
    Pass2ConsolidationOutput,
)
from app.services import consolidation as consolidation_svc
from app.workers.tasks import purge_expired_audio_task


@pytest.mark.asyncio
async def test_pass2_preserves_user_edit_locks_even_if_llm_drops_or_mutates(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = auth_user["headers"]

    create = await client.post(
        "/sessions",
        headers=headers,
        json={"title": "Untitled Meeting", "audio_retention": "retain_7d"},
    )
    assert create.status_code == 201
    session_id = create.json()["id"]

    # Seed transcript segments + an AI block + a user-locked block
    async with database.AsyncSessionLocal() as db:
        sec_auth = AgendaSection(
            session_id=session_id,
            title="Authentication & Security",
            start_ms=0,
            order_index=0,
        )
        db.add(sec_auth)
        await db.flush()

        seg1 = TranscriptSegment(
            session_id=session_id,
            text="We decided to migrate authentication to OAuth2 with PKCE and rotating JWT refresh tokens.",
            start_ms=1000,
            end_ms=5000,
            confidence=0.98,
            words_json=[],
        )
        db.add(seg1)
        await db.flush()

        ai_blk = NoteBlock(
            session_id=session_id,
            section_id=sec_auth.id,
            block_type="bullet",
            content="Old AI draft bullet",
            source_segment_ids=[seg1.id],
            is_user_edited=False,
            order_index=0,
        )
        db.add(ai_blk)
        await db.commit()
        sec_id = sec_auth.id
        seg1_id = seg1.id

    # User creates a manual locked note via REST API
    manual = await client.post(
        f"/sessions/{session_id}/blocks",
        headers=headers,
        json={
            "section_id": sec_id,
            "block_type": "scratchpad",
            "content": "HUMAN LOCKED: Verify SOC2 audit log retention before launch",
        },
    )
    assert manual.status_code == 201
    locked_id = manual.json()["id"]
    assert manual.json()["is_user_edited"] is True

    # Simulate an adversarial LLM Pass 2 response that tries to overwrite the locked block's text AND omits another copy
    async def _adversarial_pass2(session, segments, sections, blocks):
        return Pass2ConsolidationOutput(
            title_suggestion="OAuth2 Security Architecture",
            executive_summary="The team decided to adopt OAuth2 with PKCE and rotating JWT refresh tokens.",
            sections=[
                ConsolidatedSectionSpec(
                    existing_section_id=sec_id,
                    title="Authentication & Security",
                    start_ms=0,
                    blocks=[
                        ConsolidatedBlockSpec(
                            locked_block_id=locked_id,
                            block_type="bullet",
                            content="LLM TRIED TO OVERWRITE THIS TEXT",
                            source_segment_ids=[seg1_id],
                        ),
                        ConsolidatedBlockSpec(
                            locked_block_id=None,
                            block_type="decision",
                            content="Adopt OAuth2 with PKCE and rotating JWT refresh tokens.",
                            source_segment_ids=[seg1_id],
                        ),
                    ],
                )
            ],
        )

    monkeypatch.setattr(consolidation_svc, "_call_gemini_pass2", _adversarial_pass2)

    stop = await client.post(
        f"/sessions/{session_id}/stop?run_inline=true",
        headers=headers,
    )
    assert stop.status_code == 200
    detail = stop.json()
    assert detail["status"] == "completed"
    assert detail["title"] == "OAuth2 Security Architecture"

    locked_blocks = [b for b in detail["blocks"] if b["id"] == locked_id]
    assert len(locked_blocks) == 1
    # Exact human text & lock status MUST be preserved!
    assert (
        locked_blocks[0]["content"]
        == "HUMAN LOCKED: Verify SOC2 audit log retention before launch"
    )
    assert locked_blocks[0]["block_type"] == "scratchpad"
    assert locked_blocks[0]["is_user_edited"] is True
    # Citations got enriched safely without mutating content
    assert seg1_id in locked_blocks[0]["source_segment_ids"]

    # Old unedited AI draft was replaced by clean consolidated decision
    contents = [b["content"] for b in detail["blocks"]]
    assert "Old AI draft bullet" not in contents
    assert "Adopt OAuth2 with PKCE and rotating JWT refresh tokens." in contents

    # Verify Hybrid Search jumps directly to the matching Auth section
    search = await client.get(
        "/search",
        headers=headers,
        params={"q": "what did we decide about auth?"},
    )
    assert search.status_code == 200
    hits = search.json()["hits"]
    assert len(hits) >= 1
    assert hits[0]["session_id"] == session_id
    assert hits[0]["section_id"] == sec_id


@pytest.mark.asyncio
async def test_audio_retention_delete_and_expiry_purge_cron(
    client: AsyncClient, auth_user: dict
) -> None:
    headers = auth_user["headers"]

    # Session 1: delete_after_processing
    s1 = (
        await client.post(
            "/sessions",
            headers=headers,
            json={"title": "Ephemeral Call", "audio_retention": "delete_after_processing"},
        )
    ).json()["id"]

    # Write fake spool audio
    from app.services.storage import audio_storage

    audio_storage.append_pcm_chunk(s1, 0, b"\x01\x02" * 1600)
    assert audio_storage.spool_path(s1).exists()

    stop1 = await client.post(f"/sessions/{s1}/stop?run_inline=true", headers=headers)
    assert stop1.status_code == 200
    assert stop1.json()["audio_available"] is False
    assert not audio_storage.spool_path(s1).exists()

    # Session 2: retain_7d then force expiry and run purge_expired_audio_task
    s2 = (
        await client.post(
            "/sessions",
            headers=headers,
            json={"title": "Retained Call", "audio_retention": "retain_7d"},
        )
    ).json()["id"]
    audio_storage.append_pcm_chunk(s2, 0, b"\x03\x04" * 1600)

    stop2 = await client.post(f"/sessions/{s2}/stop?run_inline=true", headers=headers)
    assert stop2.status_code == 200
    assert stop2.json()["audio_available"] is True

    # Audio playback endpoint works (both full 200 and Range 206)
    audio_resp = await client.get(f"/sessions/{s2}/audio", headers=headers)
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"].startswith("audio/wav")

    range_resp = await client.get(
        f"/sessions/{s2}/audio",
        headers={**headers, "Range": "bytes=0-43"},
    )
    assert range_resp.status_code == 206
    assert len(range_resp.content) == 44

    # Backdate expiry & trigger cron purge
    async with database.AsyncSessionLocal() as db:
        res = await db.execute(select(Session).where(Session.id == s2))
        sess = res.scalar_one()
        sess.audio_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()

    purged_count = await purge_expired_audio_task({})
    assert purged_count == 1

    after_purge = await client.get(f"/sessions/{s2}", headers=headers)
    assert after_purge.json()["audio_available"] is False


@pytest.mark.asyncio
async def test_pass1_schema_validation_and_omitted_locked_block_recovery(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import ValidationError
    from app.schemas.llm_patches import Pass1PatchOp

    # Empty source_segment_ids must fail strict schema validation
    with pytest.raises(ValidationError):
        Pass1PatchOp(
            action="append_bullet",
            content="Unsupported claim",
            source_segment_ids=[],
        )

    headers = auth_user["headers"]
    sess_id = (
        await client.post("/sessions", headers=headers, json={"title": "Lock Recovery"})
    ).json()["id"]

    blk = (
        await client.post(
            f"/sessions/{sess_id}/blocks",
            headers=headers,
            json={"block_type": "scratchpad", "content": "Do not drop me"},
        )
    ).json()
    blk_id = blk["id"]

    # Patch block & action toggle check
    patched = await client.patch(
        f"/blocks/{blk_id}",
        headers=headers,
        json={"content": "Do not drop me ever"},
    )
    assert patched.status_code == 200
    assert patched.json()["is_user_edited"] is True

    # Simulate LLM returning empty sections (omitting the locked block completely)
    async def _omitting_llm(session, segments, sections, blocks):
        return Pass2ConsolidationOutput(
            executive_summary="Summary without locked blocks.",
            sections=[],
        )

    monkeypatch.setattr(consolidation_svc, "_call_gemini_pass2", _omitting_llm)
    stopped = await client.post(
        f"/sessions/{sess_id}/stop?run_inline=true", headers=headers
    )
    assert stopped.status_code == 200
    blocks_after = stopped.json()["blocks"]
    assert any(
        b["id"] == blk_id
        and b["content"] == "Do not drop me ever"
        and b["is_user_edited"] is True
        for b in blocks_after
    )


def test_remap_pass2_output_ids() -> None:
    seg_uuid_1 = "11111111-1111-1111-1111-111111111111"
    seg_uuid_2 = "22222222-2222-2222-2222-222222222222"
    sec_uuid_1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    locked_uuid_1 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    output = Pass2ConsolidationOutput(
        title_suggestion="Clean Title",
        executive_summary="Summary text.",
        sections=[
            ConsolidatedSectionSpec(
                existing_section_id="[s1]",
                title="Auth",
                start_ms=0,
                blocks=[
                    ConsolidatedBlockSpec(
                        locked_block_id="L1",
                        block_type="scratchpad",
                        content="Locked note",
                        source_segment_ids=["1", "[2]", "1", "invalid"],
                    ),
                    ConsolidatedBlockSpec(
                        locked_block_id="L99",
                        block_type="decision",
                        content="Decision",
                        source_segment_ids=[seg_uuid_2],
                    ),
                ],
            ),
            ConsolidatedSectionSpec(
                existing_section_id="S99",
                title="New Topic",
                start_ms=5000,
                blocks=[],
            ),
        ],
    )

    remapped = consolidation_svc.remap_pass2_output_ids(
        output,
        seg_id_map={"1": seg_uuid_1, "2": seg_uuid_2},
        sec_id_map={"S1": sec_uuid_1},
        locked_id_map={"L1": locked_uuid_1},
    )
    assert remapped.sections[0].existing_section_id == sec_uuid_1
    assert remapped.sections[0].blocks[0].locked_block_id == locked_uuid_1
    assert remapped.sections[0].blocks[0].source_segment_ids == [seg_uuid_1, seg_uuid_2]
    assert remapped.sections[0].blocks[1].locked_block_id is None
    assert remapped.sections[0].blocks[1].source_segment_ids == [seg_uuid_2]
    assert remapped.sections[1].existing_section_id is None


@pytest.mark.asyncio
async def test_export_cache_and_query_embedding_cache(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from app.core.config import settings
    from app.services import embeddings as embeddings_svc

    headers = auth_user["headers"]
    sess_id = (
        await client.post("/sessions", headers=headers, json={"title": "Cache Test"})
    ).json()["id"]

    # Export cache check (uses non-mock path with stubbed Gemini client)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-live-key")
    gemini_export_calls = 0

    async def _fake_generate_content(**kwargs):
        nonlocal gemini_export_calls
        gemini_export_calls += 1
        return SimpleNamespace(
            text=f"*Slack update #{gemini_export_calls}*",
            usage_metadata=None,
        )

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=_fake_generate_content)
        )
    )
    monkeypatch.setattr(consolidation_svc, "get_genai_client", lambda timeout_ms=30_000: fake_client)

    r1 = await client.post(
        f"/sessions/{sess_id}/export",
        headers=headers,
        json={"format": "slack_recap"},
    )
    assert r1.status_code == 200
    assert r1.json()["content"] == "*Slack update #1*"

    r2 = await client.post(
        f"/sessions/{sess_id}/export",
        headers=headers,
        json={"format": "slack_recap"},
    )
    assert r2.status_code == 200
    assert r2.json()["content"] == "*Slack update #1*"
    assert gemini_export_calls == 1

    # Adding a block changes markdown hash -> invalidates cache
    add_blk = await client.post(
        f"/sessions/{sess_id}/blocks",
        headers=headers,
        json={"block_type": "bullet", "content": "New decision bullet"},
    )
    assert add_blk.status_code == 201

    r3 = await client.post(
        f"/sessions/{sess_id}/export",
        headers=headers,
        json={"format": "slack_recap"},
    )
    assert r3.status_code == 200
    assert r3.json()["content"] == "*Slack update #2*"
    assert gemini_export_calls == 2

    # Query embedding cache check
    embed_calls = 0

    async def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
        nonlocal embed_calls
        embed_calls += 1
        return [[0.25] * embeddings_svc.EMBEDDING_DIM for _ in texts]

    monkeypatch.setattr(embeddings_svc, "embed_texts", _fake_embed_texts)
    embeddings_svc.clear_query_embedding_cache()

    v1 = await embeddings_svc.embed_query("  OAuth2   PKCE ")
    v2 = await embeddings_svc.embed_query("oauth2 pkce")
    assert v1 == v2
    assert embed_calls == 1


@pytest.mark.asyncio
async def test_stop_session_uploads_audio_before_worker_consolidation(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.routers import sessions as sessions_router
    from app.services.storage import audio_storage

    headers = auth_user["headers"]
    sess_id = (
        await client.post(
            "/sessions",
            headers=headers,
            json={"title": "Pre-Upload Worker Test", "audio_retention": "retain_7d"},
        )
    ).json()["id"]

    audio_storage.append_pcm_chunk(sess_id, 0, b"\x05\x06" * 1600)
    assert audio_storage.spool_path(sess_id).exists()

    pre_worker_checks: dict[str, object] = {}

    async def _capture_enqueue(session_id: str, run_inline: bool = False) -> None:
        async with database.AsyncSessionLocal() as db:
            res = await db.execute(select(Session).where(Session.id == session_id))
            sess = res.scalar_one()
            pre_worker_checks["audio_s3_key"] = sess.audio_s3_key
            pre_worker_checks["spool_exists"] = audio_storage.spool_path(session_id).exists()

    monkeypatch.setattr(
        sessions_router, "enqueue_or_run_consolidation", _capture_enqueue
    )

    stop = await client.post(f"/sessions/{sess_id}/stop", headers=headers)
    assert stop.status_code == 200
    assert pre_worker_checks["audio_s3_key"] == f"sessions/{sess_id}/audio.wav"
    assert pre_worker_checks["spool_exists"] is False

    # Worker now runs in a separate container with zero access to the API spool
    event = await consolidation_svc.consolidate_session(sess_id)
    assert event is not None
    assert event.audio_available is True

    audio_resp = await client.get(f"/sessions/{sess_id}/audio", headers=headers)
    assert audio_resp.status_code == 200
    assert audio_resp.content[:4] == b"RIFF"



