from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import GlossaryTerm, User
from app.schemas.api_schemas import GlossaryTermCreateRequest, GlossaryTermResponse

router = APIRouter(prefix="/glossary", tags=["glossary"])


@router.get("", response_model=list[GlossaryTermResponse])
async def list_glossary_terms(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[GlossaryTermResponse]:
    result = await db.execute(
        select(GlossaryTerm)
        .where(GlossaryTerm.user_id == current_user.id)
        .order_by(GlossaryTerm.created_at.desc())
    )
    terms = result.scalars().all()
    return [GlossaryTermResponse.model_validate(t) for t in terms]


@router.post(
    "", response_model=GlossaryTermResponse, status_code=status.HTTP_201_CREATED
)
async def create_glossary_term(
    payload: GlossaryTermCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlossaryTermResponse:
    cleaned = payload.term.strip()
    existing = await db.execute(
        select(GlossaryTerm).where(
            GlossaryTerm.user_id == current_user.id,
            GlossaryTerm.term == cleaned,
        )
    )
    term_obj = existing.scalar_one_or_none()
    if term_obj is not None:
        term_obj.boost = payload.boost
        await db.commit()
        await db.refresh(term_obj)
        return GlossaryTermResponse.model_validate(term_obj)

    term_obj = GlossaryTerm(
        user_id=current_user.id,
        term=cleaned,
        boost=payload.boost,
    )
    db.add(term_obj)
    await db.commit()
    await db.refresh(term_obj)
    return GlossaryTermResponse.model_validate(term_obj)


@router.delete("/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_glossary_term(
    term_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(GlossaryTerm).where(
            GlossaryTerm.id == term_id,
            GlossaryTerm.user_id == current_user.id,
        )
    )
    term_obj = result.scalar_one_or_none()
    if term_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Glossary term not found"
        )
    await db.delete(term_obj)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
