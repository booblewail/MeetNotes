from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auth_register_login_and_me(client: AsyncClient) -> None:
    reg = await client.post(
        "/auth/register",
        json={
            "email": "bob@meetnotes.dev",
            "password": "password123",
            "full_name": "Bob Builder",
        },
    )
    assert reg.status_code == 201
    reg_data = reg.json()
    assert reg_data["user"]["email"] == "bob@meetnotes.dev"
    assert reg_data["access_token"]

    # Duplicate email returns 409
    dup = await client.post(
        "/auth/register",
        json={
            "email": "bob@meetnotes.dev",
            "password": "password123",
            "full_name": "Bob Duplicate",
        },
    )
    assert dup.status_code == 409

    # Login succeeds
    login = await client.post(
        "/auth/login",
        json={"email": "bob@meetnotes.dev", "password": "password123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["full_name"] == "Bob Builder"

    # Update retention preference
    patch = await client.patch(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"default_audio_retention": "retain_30d"},
    )
    assert patch.status_code == 200
    assert patch.json()["default_audio_retention"] == "retain_30d"


@pytest.mark.asyncio
async def test_session_crud_glossary_and_exports(
    client: AsyncClient, auth_user: dict
) -> None:
    headers = auth_user["headers"]

    # Glossary CRUD
    g_add = await client.post(
        "/glossary",
        headers=headers,
        json={"term": "pgvector", "boost": 2.5},
    )
    assert g_add.status_code == 201
    term_id = g_add.json()["id"]

    g_list = await client.get("/glossary", headers=headers)
    assert g_list.status_code == 200
    assert len(g_list.json()) == 1
    assert g_list.json()[0]["term"] == "pgvector"

    # Create session
    create = await client.post(
        "/sessions",
        headers=headers,
        json={"title": "Q4 Architecture Review"},
    )
    assert create.status_code == 201
    session_id = create.json()["id"]

    # Add manual scratchpad block (sets is_user_edited = True)
    blk = await client.post(
        f"/sessions/{session_id}/blocks",
        headers=headers,
        json={
            "block_type": "action_item",
            "content": "Share pgvector benchmark report",
            "assignee": "Alice",
            "due_date_text": "Friday",
        },
    )
    assert blk.status_code == 201
    blk_data = blk.json()
    assert blk_data["is_user_edited"] is True

    # Export Markdown, Slack, Email
    for fmt in ("markdown", "slack_recap", "followup_email"):
        exp = await client.post(
            f"/sessions/{session_id}/export",
            headers=headers,
            json={"format": fmt},
        )
        assert exp.status_code == 200
        assert "pgvector" in exp.json()["content"]

    # Delete glossary term & session
    g_del = await client.delete(f"/glossary/{term_id}", headers=headers)
    assert g_del.status_code == 204

    s_del = await client.delete(f"/sessions/{session_id}", headers=headers)
    assert s_del.status_code == 204
