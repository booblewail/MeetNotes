from __future__ import annotations

import os
import struct
import time
import wave
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import AsyncClient
import pytest

from app.core.config import settings
from app.main import app as fastapi_app
from app.services import consolidation as consolidation_svc
from app.services import embeddings as embeddings_svc
from app.services import stt_stream as stt_svc
from app.services import structuring as structuring_svc
from app.services.structuring import run_pass1_if_ready

RUN_LIVE_AI = os.environ.get("RUN_LIVE_AI_TESTS") == "1"
DEFAULT_WAV = Path(
    "/Users/calvinzhao/.gemini/antigravity/brain/2a31dda1-25f8-4de9-817e-14ba83519c23/scratch/live_meeting.wav"
)


@pytest.mark.live_ai
@pytest.mark.skipif(not RUN_LIVE_AI, reason="Set RUN_LIVE_AI_TESTS=1 to run live cloud E2E")
@pytest.mark.asyncio
async def test_live_deepgram_and_gemini_end_to_end(
    client: AsyncClient, auth_user: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not settings.is_mock_stt, "DEEPGRAM_API_KEY must be configured in .env"
    assert not settings.is_mock_llm, "GEMINI_API_KEY must be configured in .env"

    wav_path = Path(os.environ.get("LIVE_WAV_PATH", str(DEFAULT_WAV)))
    assert wav_path.exists(), f"Missing WAV fixture: {wav_path}"

    # Guarantee zero silent fallbacks to mock implementations
    def _fail_mock(*args, **kwargs):
        raise AssertionError("Unexpected fallback to mock implementation during live AI test")

    monkeypatch.setattr(structuring_svc, "_mock_pass1_patches", _fail_mock)
    monkeypatch.setattr(consolidation_svc, "_mock_pass2_consolidation", _fail_mock)
    monkeypatch.setattr(embeddings_svc, "deterministic_embedding", _fail_mock)
    monkeypatch.setattr(stt_svc.MockLiveStream, "start", _fail_mock)

    headers = auth_user["headers"]
    token = auth_user["token"]

    # Add custom glossary keyterms to test Deepgram Nova-3 keyterm boosting
    for term in ("pgvector", "OAuth2", "PKCE"):
        g = await client.post(
            "/glossary", headers=headers, json={"term": term, "boost": 2.0}
        )
        assert g.status_code == 201

    create = await client.post(
        "/sessions",
        headers=headers,
        json={"title": "Untitled Meeting", "audio_retention": "retain_7d"},
    )
    assert create.status_code == 201
    session_id = create.json()["id"]

    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        pcm_all = wf.readframes(wf.getnframes())

    chunk_bytes = 3200  # 100ms at 16kHz mono PCM16
    trailing_silence = b"\x00" * chunk_bytes

    final_segments: list[dict] = []
    pass1_patches: list[dict] = []

    with TestClient(fastapi_app) as sync_client:
        with sync_client.websocket_connect(
            f"/ws/sessions/{session_id}?token={token}"
        ) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "session.ready"
            assert ready["stt_mode"] == "deepgram"
            assert ready["llm_mode"] == "gemini"

            offset_ms = 0
            for idx in range(0, len(pcm_all), chunk_bytes):
                chunk = pcm_all[idx : idx + chunk_bytes]
                ws.send_bytes(struct.pack("<I", offset_ms) + chunk)
                offset_ms += 100
                time.sleep(0.04)

            # Stream 2.5s trailing silence so Deepgram emits final utterance boundaries
            for _ in range(25):
                ws.send_bytes(struct.pack("<I", offset_ms) + trailing_silence)
                offset_ms += 100
                time.sleep(0.05)

            # Give Deepgram a brief window to flush final transcript events
            time.sleep(1.5)

            # Drain events from the socket up to our sync_state sentinel
            ws.send_text('{"type":"sync_state"}')
            for _ in range(120):
                evt = ws.receive_json()
                etype = evt.get("type")
                if etype == "transcript.final":
                    final_segments.append(evt["segment"])
                elif etype == "notes.patch":
                    pass1_patches.append(evt)
                elif etype == "session.status":
                    break

    print(f"\n[LIVE DEEPGRAM] Received {len(final_segments)} final segments:", flush=True)
    for seg in final_segments:
        print(
            f"  - [{seg['start_ms']}ms] {seg['speaker_label']} (conf={seg['confidence']}): {seg['text']}",
            flush=True,
        )

    assert len(final_segments) >= 2
    full_transcript = " ".join(s["text"].lower() for s in final_segments)
    assert any(k in full_transcript for k in ("oauth", "jwt", "authentication"))
    assert any(k in full_transcript for k in ("pgvector", "postgres", "database"))

    # Trigger Pass 1 structuring explicitly on remaining pending utterances
    patch = await run_pass1_if_ready(session_id, force=True)
    if patch:
        pass1_patches.append(patch.model_dump())
    print(f"[LIVE GEMINI FLASH] Pass 1 patch batches={len(pass1_patches)}", flush=True)
    assert len(pass1_patches) >= 1

    # Add a human-locked scratchpad note that Pass 2 MUST preserve verbatim
    locked_resp = await client.post(
        f"/sessions/{session_id}/blocks",
        headers=headers,
        json={
            "block_type": "scratchpad",
            "content": "HUMAN LOCKED: Confirm SOC2 audit log retention before launch",
        },
    )
    assert locked_resp.status_code == 201
    locked_id = locked_resp.json()["id"]

    # Run Pass 2 consolidation via Gemini Pro + Gemini Embeddings
    stop_resp = await client.post(
        f"/sessions/{session_id}/stop?run_inline=true", headers=headers
    )
    assert stop_resp.status_code == 200
    detail = stop_resp.json()
    print(f"[LIVE GEMINI PRO] Title: {detail['title']}")
    print(f"[LIVE GEMINI PRO] Executive Summary: {detail['executive_summary']}")
    print(
        f"[LIVE GEMINI PRO] Sections={len(detail['sections'])}, Blocks={len(detail['blocks'])}"
    )

    assert detail["status"] == "completed"
    assert detail["executive_summary"]
    assert len(detail["sections"]) >= 1
    assert len(detail["blocks"]) >= 2
    assert detail["audio_available"] is True

    # Verify human lock survived Pass 2 verbatim
    locked_after = [b for b in detail["blocks"] if b["id"] == locked_id]
    assert len(locked_after) == 1
    assert (
        locked_after[0]["content"]
        == "HUMAN LOCKED: Confirm SOC2 audit log retention before launch"
    )
    assert locked_after[0]["is_user_edited"] is True

    # Verify Hybrid Search using live Gemini Embeddings
    search_resp = await client.get(
        "/search", headers=headers, params={"q": "what did we decide about auth?"}
    )
    assert search_resp.status_code == 200
    hits = search_resp.json()["hits"]
    print(f"[LIVE HYBRID SEARCH] Hits={len(hits)}")
    assert len(hits) >= 1
    assert hits[0]["session_id"] == session_id

    # Verify live Gemini Flash exports
    for fmt in ("slack_recap", "followup_email"):
        exp = await client.post(
            f"/sessions/{session_id}/export",
            headers=headers,
            json={"format": fmt},
        )
        assert exp.status_code == 200
        assert len(exp.json()["content"]) > 30
