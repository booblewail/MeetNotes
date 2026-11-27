from __future__ import annotations

import json
import struct

from fastapi.testclient import TestClient
from httpx import AsyncClient
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_ws_audio_utterance_pass1_and_redaction(
    client: AsyncClient, auth_user: dict
) -> None:
    headers = auth_user["headers"]
    token = auth_user["token"]

    create = await client.post(
        "/sessions",
        headers=headers,
        json={"title": "Live Pipeline Test", "audio_retention": "retain_7d"},
    )
    assert create.status_code == 201
    session_id = create.json()["id"]

    with TestClient(app) as sync_client:
        with sync_client.websocket_connect(
            f"/ws/sessions/{session_id}?token={token}"
        ) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "session.ready"
            assert ready["session_id"] == session_id

            # Send binary PCM16 frame prefixed with 4-byte uint32 LE offset_ms (1000ms)
            pcm_silence = b"\x00\x01" * 1600  # 100ms of 16kHz mono PCM16
            frame = struct.pack("<I", 1000) + pcm_silence
            ws.send_bytes(frame)

            # Inject a low-confidence utterance (~0.62 < 0.75) and a high-confidence decision
            ws.send_text(
                json.dumps(
                    {
                        "type": "simulate_utterance",
                        "speaker_label": "Speaker 1",
                        "text": "Let's move to authentication and security review.",
                        "start_ms": 2000,
                        "end_ms": 5000,
                        "confidence": 0.62,
                        "force_pass1": False,
                    }
                )
            )
            seg1_evt = ws.receive_json()
            assert seg1_evt["type"] == "transcript.final"
            assert seg1_evt["segment"]["has_low_confidence"] is True
            speaker_id = seg1_evt["speaker"]["id"]

            # Second utterance pushes across or forces Pass 1 structuring
            ws.send_text(
                json.dumps(
                    {
                        "type": "simulate_utterance",
                        "speaker_label": "Speaker 2",
                        "text": "We decided to use OAuth2 with short-lived JWT tokens. Alice will implement the token refresh endpoint by Friday.",
                        "start_ms": 6000,
                        "end_ms": 11000,
                        "confidence": 0.96,
                        "force_pass1": True,
                    }
                )
            )
            seen_types: list[str] = []
            notes_patch = None
            for _ in range(4):
                msg = ws.receive_json()
                seen_types.append(msg["type"])
                if msg["type"] == "notes.patch":
                    notes_patch = msg
                    break
            assert "transcript.final" in seen_types
            assert notes_patch is not None
            assert len(notes_patch["created_sections"]) >= 1
            assert len(notes_patch["created_blocks"]) >= 1
            # Low confidence on seg1 propagated into created block
            assert any(b["has_low_confidence"] for b in notes_patch["created_blocks"])
            # Every block carries non-empty source_segment_ids
            for blk in notes_patch["created_blocks"]:
                assert len(blk["source_segment_ids"]) >= 1

            # Pin moment
            ws.send_text(
                json.dumps(
                    {
                        "type": "pin_moment",
                        "offset_ms": 9500,
                        "label": "JWT decision",
                    }
                )
            )
            pin_evt = ws.receive_json()
            assert pin_evt["type"] == "moment.pinned"
            assert pin_evt["moment"]["offset_ms"] == 9500

            # Sensitive utterance in the latest window
            ws.send_text(
                json.dumps(
                    {
                        "type": "simulate_utterance",
                        "speaker_label": "Speaker 1",
                        "text": "Actually the secret vendor quote is forty thousand dollars.",
                        "start_ms": 180000,
                        "end_ms": 184000,
                        "confidence": 0.95,
                        "force_pass1": True,
                    }
                )
            )
            for _ in range(4):
                msg = ws.receive_json()
                if msg["type"] == "notes.patch":
                    break

            # Redact last 2 minutes (120000ms) around 185000ms -> [65000, 185000]
            ws.send_text(
                json.dumps(
                    {
                        "type": "redact_window",
                        "window_ms": 120000,
                        "current_offset_ms": 185000,
                    }
                )
            )
            redacted_evt = ws.receive_json()
            assert redacted_evt["type"] == "session.redacted"
            assert len(redacted_evt["redacted_segment_ids"]) == 1
            assert len(redacted_evt["deleted_block_ids"]) >= 1

    # Verify speaker rename propagates to session detail
    ren = await client.patch(
        f"/sessions/{session_id}/speakers/{speaker_id}",
        headers=headers,
        json={"display_name": "Alex Rivera"},
    )
    assert ren.status_code == 200
    assert ren.json()["display_name"] == "Alex Rivera"

    detail = await client.get(f"/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200
    data = detail.json()
    assert len(data["pinned_moments"]) == 1
    # Redacted utterance must not appear in visible transcript segments
    assert all("forty thousand" not in seg["text"] for seg in data["segments"])
    assert any(seg["speaker_label"] == "Alex Rivera" for seg in data["segments"])


@pytest.mark.asyncio
async def test_ws_word_threshold_pass1_and_multi_device_pubsub_sync(
    client: AsyncClient, auth_user: dict
) -> None:
    headers = auth_user["headers"]
    token = auth_user["token"]

    create = await client.post(
        "/sessions",
        headers=headers,
        json={"title": "Multi-Device Sync Test"},
    )
    assert create.status_code == 201
    session_id = create.json()["id"]

    # Generate > 100 words without force_pass1 to verify automatic sliding-window trigger
    long_utterance = " ".join(
        [
            "We discussed database migration planning and decided to use pgvector with PostgreSQL 16 for semantic search.",
            "Every agenda section will generate a 768-dimensional embedding vector alongside full-text tsvector indexing.",
            "Bob will verify index performance on ten thousand synthetic meeting notes before next Wednesday.",
            "We also reviewed mobile audio capture latency and confirmed Silero VAD drops silent intervals cleanly.",
            "The team agreed that keeping WebSocket frames multiplexed prevents phone and laptop sync drift.",
            "Let us check whether Redis Pub/Sub delivers patches to both connected devices within fifty milliseconds.",
            "Finally we need to confirm retention defaults and ensure temporary PCM spools are removed after upload.",
        ]
    )
    assert len(long_utterance.split()) >= 100

    with TestClient(app) as sync_client:
        with (
            sync_client.websocket_connect(f"/ws/sessions/{session_id}?token={token}") as ws_phone,
            sync_client.websocket_connect(f"/ws/sessions/{session_id}?token={token}") as ws_laptop,
        ):
            assert ws_phone.receive_json()["type"] == "session.ready"
            assert ws_laptop.receive_json()["type"] == "session.ready"

            ws_phone.send_text(
                json.dumps(
                    {
                        "type": "simulate_utterance",
                        "speaker_label": "Speaker 1",
                        "text": long_utterance,
                        "start_ms": 500,
                        "end_ms": 18000,
                        "confidence": 0.94,
                        "force_pass1": False,
                    }
                )
            )

            # Both phone and laptop sockets should receive transcript.final and notes.patch
            for ws_peer in (ws_phone, ws_laptop):
                types_seen = set()
                for _ in range(5):
                    evt = ws_peer.receive_json()
                    types_seen.add(evt["type"])
                    if {"transcript.final", "notes.patch"}.issubset(types_seen):
                        break
                assert "transcript.final" in types_seen
                assert "notes.patch" in types_seen

            # Stop session via WS and verify consolidated broadcast
            ws_phone.send_text(json.dumps({"type": "stop_session"}))
            consolidated = None
            for _ in range(6):
                evt = ws_laptop.receive_json()
                if evt["type"] == "session.consolidated":
                    consolidated = evt
                    break
            assert consolidated is not None
            assert consolidated["status"] == "completed"
            assert consolidated["executive_summary"]


@pytest.mark.asyncio
async def test_pass1_timer_gating_min_words_and_60s_window(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import database
    from app.core.redis import broker
    from app.models import TranscriptSegment
    from app.services import structuring as structuring_svc

    headers = auth_user["headers"]
    session_id = (
        await client.post(
            "/sessions",
            headers=headers,
            json={"title": "Timer Gating Test"},
        )
    ).json()["id"]

    async def _seed_utterances(word_count: int) -> None:
        text = " ".join(f"word{i}" for i in range(word_count))
        async with database.AsyncSessionLocal() as db:
            seg = TranscriptSegment(
                session_id=session_id,
                text=text,
                start_ms=1000,
                end_ms=4000,
                confidence=0.95,
                words_json=[],
                is_redacted=False,
            )
            db.add(seg)
            await db.commit()
            seg_id = seg.id

        await broker.clear_pending_utterances(session_id)
        await broker.push_pending_utterance(
            session_id,
            {
                "id": seg_id,
                "speaker_label": "Speaker 1",
                "text": text,
                "start_ms": 1000,
                "end_ms": 4000,
                "confidence": 0.95,
            },
        )

    clock = {"now": 100.0}
    monkeypatch.setattr(structuring_svc.time, "monotonic", lambda: clock["now"])

    # Case 1: elapsed=45s (<60s), 30 words (<100) -> does NOT trigger
    await _seed_utterances(30)
    await broker.set_last_pass1_ts(session_id, 55.0)
    assert await structuring_svc.run_pass1_if_ready(session_id, force=False) is None

    # Case 2: elapsed=65s (>=60s), 10 words (<25) -> does NOT trigger
    await _seed_utterances(10)
    await broker.set_last_pass1_ts(session_id, 35.0)
    assert await structuring_svc.run_pass1_if_ready(session_id, force=False) is None

    # Case 3: elapsed=65s (>=60s), 25 words (>=25) -> DOES trigger
    await _seed_utterances(25)
    await broker.set_last_pass1_ts(session_id, 35.0)
    evt_timer = await structuring_svc.run_pass1_if_ready(session_id, force=False)
    assert evt_timer is not None
    assert len(evt_timer.created_blocks) >= 1

    # Case 4: elapsed=1s (<60s), 100 words (>=100) -> DOES trigger immediately
    await _seed_utterances(100)
    await broker.set_last_pass1_ts(session_id, 99.0)
    evt_words = await structuring_svc.run_pass1_if_ready(session_id, force=False)
    assert evt_words is not None
    assert len(evt_words.created_blocks) >= 1


def test_remap_pass1_batch_ids() -> None:
    from app.schemas.llm_patches import Pass1PatchBatch, Pass1PatchOp
    from app.services.structuring import remap_pass1_batch_ids

    seg_uuid_1 = "11111111-1111-1111-1111-111111111111"
    seg_uuid_2 = "22222222-2222-2222-2222-222222222222"
    sec_uuid_1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    batch = Pass1PatchBatch(
        operations=[
            Pass1PatchOp(
                action="append_bullet",
                section_id="[s1]",
                content="Point one",
                source_segment_ids=["1", "[2]", "1", seg_uuid_2, "999"],
            ),
            Pass1PatchOp(
                action="add_decision",
                section_id=sec_uuid_1,
                content="Decision with raw UUID",
                source_segment_ids=[seg_uuid_1],
            ),
            Pass1PatchOp(
                action="append_bullet",
                section_id="S99",
                content="Invalid segment citations should drop op",
                source_segment_ids=["999"],
            ),
        ]
    )

    remapped = remap_pass1_batch_ids(
        batch,
        seg_id_map={"1": seg_uuid_1, "2": seg_uuid_2},
        sec_id_map={"S1": sec_uuid_1},
    )
    assert len(remapped.operations) == 2
    assert remapped.operations[0].section_id == sec_uuid_1
    assert remapped.operations[0].source_segment_ids == [seg_uuid_1, seg_uuid_2]
    assert remapped.operations[1].section_id == sec_uuid_1
    assert remapped.operations[1].source_segment_ids == [seg_uuid_1]


def test_coalesce_utterances_and_expanded_remap() -> None:
    from app.schemas.llm_patches import Pass1PatchBatch, Pass1PatchOp
    from app.services.structuring import coalesce_utterances, remap_pass1_batch_ids

    u1 = {
        "id": "11111111-1111-1111-1111-111111111111",
        "speaker_label": "Speaker 1",
        "start_ms": 1000,
        "end_ms": 2000,
        "text": "We decided to adopt",
    }
    u2 = {
        "id": "22222222-2222-2222-2222-222222222222",
        "speaker_label": "Speaker 1",
        "start_ms": 3200,  # 1200ms gap <= 2500ms -> merges with u1
        "end_ms": 4800,
        "text": "OAuth2 with PKCE.",
    }
    u3 = {
        "id": "33333333-3333-3333-3333-333333333333",
        "speaker_label": "Speaker 2",  # Speaker switch -> new turn
        "start_ms": 5100,
        "end_ms": 6200,
        "text": "Sounds great.",
    }
    u4 = {
        "id": "44444444-4444-4444-4444-444444444444",
        "speaker_label": "Speaker 2",
        "start_ms": 12000,  # 5800ms gap > 2500ms -> new turn
        "end_ms": 13500,
        "text": "Let us check Postgres.",
    }

    turns = coalesce_utterances([u1, u2, u3, u4])
    assert len(turns) == 3
    assert turns[0]["segment_ids"] == [u1["id"], u2["id"]]
    assert turns[0]["text"] == "We decided to adopt OAuth2 with PKCE."
    assert turns[1]["segment_ids"] == [u3["id"]]
    assert turns[2]["segment_ids"] == [u4["id"]]

    seg_id_map = {str(idx): t["segment_ids"] for idx, t in enumerate(turns, start=1)}
    batch = Pass1PatchBatch(
        operations=[
            Pass1PatchOp(
                action="add_decision",
                content="Adopt OAuth2 with PKCE",
                source_segment_ids=["1", u2["id"]],
            )
        ]
    )
    remapped = remap_pass1_batch_ids(batch, seg_id_map=seg_id_map, sec_id_map={})
    assert remapped.operations[0].source_segment_ids == [u1["id"], u2["id"]]


@pytest.mark.asyncio
async def test_ws_lazy_stt_starts_only_on_audio_and_closes_on_pause(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from app.api.routers import ws as ws_router

    events_log: list[str] = []
    pass1_calls: list[bool] = []

    class SpySTTStream:
        mode = "mock"

        def __init__(self, keyterms=None):
            self._queue: asyncio.Queue = asyncio.Queue()
            self.audio_chunks = 0

        async def start(self) -> None:
            events_log.append("start")

        async def send_audio(self, offset_ms: int, pcm_bytes: bytes) -> None:
            self.audio_chunks += 1
            events_log.append("audio")

        async def next_event(self):
            return await self._queue.get()

        async def close(self) -> None:
            events_log.append("close")
            await self._queue.put(None)

    async def _spy_pass1(session_id: str, force: bool = False):
        pass1_calls.append(force)
        return None

    monkeypatch.setattr(ws_router, "create_stt_stream", lambda keyterms=None: SpySTTStream(keyterms))
    monkeypatch.setattr(ws_router, "run_pass1_if_ready", _spy_pass1)

    headers = auth_user["headers"]
    token = auth_user["token"]
    session_id = (
        await client.post("/sessions", headers=headers, json={"title": "Lazy STT"})
    ).json()["id"]

    with TestClient(app) as sync_client:
        with sync_client.websocket_connect(f"/ws/sessions/{session_id}?token={token}") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "session.ready"
            assert events_log == []

            ws.send_text(
                json.dumps(
                    {
                        "type": "simulate_utterance",
                        "speaker_label": "Speaker 1",
                        "text": "Simulated text without real audio.",
                        "start_ms": 100,
                        "end_ms": 900,
                        "confidence": 0.95,
                        "force_pass1": False,
                    }
                )
            )
            assert ws.receive_json()["type"] == "transcript.final"
            assert events_log == []

            ws.send_bytes(struct.pack("<I", 1000) + (b"\x00\x01" * 1600))
            ws.send_text(json.dumps({"type": "sync_state"}))
            assert ws.receive_json()["type"] == "session.status"
            assert events_log == ["start", "audio"]

            ws.send_text(json.dumps({"type": "pause"}))
            assert ws.receive_json()["type"] == "session.status"
            assert events_log == ["start", "audio", "close"]

            pass1_calls.clear()
            ws.send_text(json.dumps({"type": "stop_session"}))
            for _ in range(4):
                evt = ws.receive_json()
                if evt["type"] == "session.consolidated":
                    break
            assert pass1_calls == []



