from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import AgendaSection, NoteBlock, Session, User
from app.schemas.api_schemas import SearchHitResponse, SearchResponse
from app.services.embeddings import cosine_similarity, embed_query

router = APIRouter(prefix="/search", tags=["search"])


def _keyword_score(query_tokens: list[str], hay_text: str) -> float:
    if not query_tokens or not hay_text:
        return 0.0
    lower = hay_text.lower()
    hits = sum(1 for t in query_tokens if t in lower)
    if hits == 0:
        return 0.0
    phrase = " ".join(query_tokens)
    bonus = 1.5 if phrase in lower else 0.0
    return (hits / len(query_tokens)) + bonus


@router.get("", response_model=SearchResponse)
async def hybrid_search(
    q: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(default=12, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    query = q.strip()
    query_vec = await embed_query(query)
    tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1]

    dialect_name = db.bind.dialect.name if db.bind is not None else "sqlite"

    if dialect_name == "postgresql":
        # Native PostgreSQL FTS + pgvector cosine distance combined via Reciprocal Rank Fusion
        vec_literal = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"
        sql = text(
            """
            WITH section_docs AS (
                SELECT
                    'section' AS kind,
                    s.id AS session_id,
                    s.title AS session_title,
                    sec.id AS section_id,
                    sec.title AS section_title,
                    sec.start_ms AS start_ms,
                    s.created_at AS created_at,
                    coalesce(sec.title, '') || ' ' || coalesce(string_agg(nb.content, ' '), '') AS doc_text,
                    coalesce(substring(string_agg(nb.content, ' • ') from 1 for 220), sec.title) AS snippet,
                    sec.embedding AS vec
                FROM agenda_sections sec
                JOIN sessions s ON s.id = sec.session_id
                LEFT JOIN note_blocks nb ON nb.section_id = sec.id
                WHERE s.user_id = :user_id
                GROUP BY s.id, s.title, sec.id, sec.title, sec.start_ms, s.created_at, sec.embedding
            ),
            kw AS (
                SELECT
                    section_id,
                    ROW_NUMBER() OVER (
                        ORDER BY ts_rank_cd(to_tsvector('english', doc_text), websearch_to_tsquery('english', :q)) DESC
                    ) AS kw_rank
                FROM section_docs
                WHERE to_tsvector('english', doc_text) @@ websearch_to_tsquery('english', :q)
                   OR doc_text ILIKE :ilike_q
            ),
            sem AS (
                SELECT
                    section_id,
                    ROW_NUMBER() OVER (ORDER BY vec <=> CAST(:vec AS vector) ASC) AS sem_rank
                FROM section_docs
                WHERE vec IS NOT NULL
            )
            SELECT
                d.kind,
                d.session_id,
                d.session_title,
                d.section_id,
                d.section_title,
                d.start_ms,
                d.snippet,
                d.created_at,
                kw.kw_rank,
                sem.sem_rank,
                (
                    COALESCE(1.0 / (60.0 + kw.kw_rank), 0.0) +
                    COALESCE(1.0 / (60.0 + sem.sem_rank), 0.0)
                ) AS rrf_score
            FROM section_docs d
            LEFT JOIN kw ON kw.section_id = d.section_id
            LEFT JOIN sem ON sem.section_id = d.section_id
            WHERE kw.kw_rank IS NOT NULL OR sem.sem_rank IS NOT NULL
            ORDER BY rrf_score DESC, d.created_at DESC
            LIMIT :limit
            """
        )
        rows = (
            await db.execute(
                sql,
                {
                    "user_id": current_user.id,
                    "q": query,
                    "ilike_q": f"%{query}%",
                    "vec": vec_literal,
                    "limit": limit,
                },
            )
        ).mappings().all()
        if rows:
            return SearchResponse(
                query=query,
                hits=[
                    SearchHitResponse(
                        kind=r["kind"],
                        session_id=r["session_id"],
                        session_title=r["session_title"],
                        section_id=r["section_id"],
                        section_title=r["section_title"],
                        start_ms=int(r["start_ms"] or 0),
                        snippet=r["snippet"] or r["section_title"] or "",
                        score=round(float(r["rrf_score"]), 6),
                        keyword_rank=int(r["kw_rank"]) if r["kw_rank"] else None,
                        semantic_rank=int(r["sem_rank"]) if r["sem_rank"] else None,
                        created_at=r["created_at"],
                    )
                    for r in rows
                ],
            )

    # Cross-dialect / SQLite / Session-summary fallback RRF scoring
    sess_res = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id)
        .options(selectinload(Session.sections), selectinload(Session.blocks))
        .order_by(Session.created_at.desc())
    )
    sessions = list(sess_res.scalars().all())

    candidates: list[dict] = []
    for sess in sessions:
        blocks_by_sec: dict[str, list[NoteBlock]] = {}
        for blk in sess.blocks:
            if blk.section_id:
                blocks_by_sec.setdefault(blk.section_id, []).append(blk)

        if sess.sections:
            for sec in sess.sections:
                sec_blocks = blocks_by_sec.get(sec.id, [])
                combined_text = (
                    f"{sess.title} {sec.title} "
                    + " ".join(b.content for b in sec_blocks)
                )
                snippet = (
                    " • ".join(b.content for b in sec_blocks[:2])[:220]
                    or sec.title
                )
                kw_val = _keyword_score(tokens, combined_text)
                sem_val = (
                    cosine_similarity(query_vec, sec.embedding)
                    if sec.embedding
                    else 0.0
                )
                candidates.append(
                    {
                        "kind": "section",
                        "session_id": sess.id,
                        "session_title": sess.title,
                        "section_id": sec.id,
                        "section_title": sec.title,
                        "start_ms": sec.start_ms,
                        "snippet": snippet,
                        "created_at": sess.created_at,
                        "kw_val": kw_val,
                        "sem_val": sem_val,
                    }
                )
        else:
            combined_text = f"{sess.title} {sess.executive_summary or ''}"
            kw_val = _keyword_score(tokens, combined_text)
            sem_val = (
                cosine_similarity(query_vec, sess.summary_embedding)
                if sess.summary_embedding
                else 0.0
            )
            candidates.append(
                {
                    "kind": "session",
                    "session_id": sess.id,
                    "session_title": sess.title,
                    "section_id": None,
                    "section_title": None,
                    "start_ms": 0,
                    "snippet": (sess.executive_summary or sess.title)[:220],
                    "created_at": sess.created_at,
                    "kw_val": kw_val,
                    "sem_val": sem_val,
                }
            )

    kw_ranked = sorted(
        [c for c in candidates if c["kw_val"] > 0],
        key=lambda c: c["kw_val"],
        reverse=True,
    )
    sem_ranked = sorted(
        [c for c in candidates if c["sem_val"] > 0.05],
        key=lambda c: c["sem_val"],
        reverse=True,
    )

    kw_rank_map = {id(c): idx + 1 for idx, c in enumerate(kw_ranked)}
    sem_rank_map = {id(c): idx + 1 for idx, c in enumerate(sem_ranked)}

    scored: list[tuple[float, dict]] = []
    for c in candidates:
        k_rank = kw_rank_map.get(id(c))
        s_rank = sem_rank_map.get(id(c))
        if k_rank is None and s_rank is None:
            continue
        rrf = (1.0 / (60.0 + k_rank) if k_rank else 0.0) + (
            1.0 / (60.0 + s_rank) if s_rank else 0.0
        )
        scored.append((rrf, {**c, "k_rank": k_rank, "s_rank": s_rank}))

    scored.sort(key=lambda item: item[0], reverse=True)
    return SearchResponse(
        query=query,
        hits=[
            SearchHitResponse(
                kind=item["kind"],
                session_id=item["session_id"],
                session_title=item["session_title"],
                section_id=item["section_id"],
                section_title=item["section_title"],
                start_ms=item["start_ms"],
                snippet=item["snippet"],
                score=round(score, 6),
                keyword_rank=item["k_rank"],
                semantic_rank=item["s_rank"],
                created_at=item["created_at"],
            )
            for score, item in scored[:limit]
        ],
    )
