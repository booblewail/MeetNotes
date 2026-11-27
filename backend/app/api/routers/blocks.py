from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.serializers import serialize_block, serialize_section
from app.core.database import get_db
from app.core.redis import broker
from app.models import ActionItem, AgendaSection, NoteBlock, Session, User
from app.schemas.api_schemas import (
    ActionItemUpdateRequest,
    BlockCreateRequest,
    BlockUpdateRequest,
    SectionUpdateRequest,
)
from app.schemas.ws_events import (
    ActionItemPayload,
    AgendaSectionPayload,
    BlockUpdatedEvent,
    NoteBlockPayload,
    SectionUpdatedEvent,
)

router = APIRouter(tags=["blocks"])


async def _get_owned_session(
    session_id: str, user_id: str, db: AsyncSession
) -> Session:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


@router.post(
    "/sessions/{session_id}/blocks",
    response_model=NoteBlockPayload,
    status_code=status.HTTP_201_CREATED,
)
async def create_block(
    session_id: str,
    payload: BlockCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NoteBlockPayload:
    await _get_owned_session(session_id, current_user.id, db)

    if payload.section_id:
        sec_res = await db.execute(
            select(AgendaSection).where(
                AgendaSection.id == payload.section_id,
                AgendaSection.session_id == session_id,
            )
        )
        if sec_res.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
            )

    max_order_res = await db.execute(
        select(func.coalesce(func.max(NoteBlock.order_index), -1)).where(
            NoteBlock.session_id == session_id
        )
    )
    next_order = int(max_order_res.scalar_one() or -1) + 1

    block = NoteBlock(
        session_id=session_id,
        section_id=payload.section_id,
        block_type=payload.block_type,
        content=payload.content.strip(),
        assignee=payload.assignee,
        due_date_text=payload.due_date_text,
        source_segment_ids=payload.source_segment_ids,
        is_user_edited=True,
        order_index=next_order,
    )
    db.add(block)
    await db.flush()

    if block.block_type == "action_item":
        action = ActionItem(
            session_id=session_id,
            block_id=block.id,
            description=block.content,
            assignee=block.assignee,
            due_date_text=block.due_date_text,
            completed=block.completed,
            source_segment_ids=list(block.source_segment_ids),
        )
        db.add(action)

    await db.commit()
    await db.refresh(block)
    block_dto = serialize_block(block)
    await broker.publish_session_event(
        session_id,
        BlockUpdatedEvent(
            session_id=session_id,
            action="created",
            block=block_dto,
            block_id=block.id,
        ).model_dump(),
    )
    return block_dto


@router.patch("/blocks/{block_id}", response_model=NoteBlockPayload)
async def update_block(
    block_id: str,
    payload: BlockUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NoteBlockPayload:
    result = await db.execute(
        select(NoteBlock)
        .join(Session, NoteBlock.session_id == Session.id)
        .where(NoteBlock.id == block_id, Session.user_id == current_user.id)
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note block not found"
        )

    if payload.content is not None:
        block.content = payload.content.strip()
        block.is_user_edited = True
    if payload.block_type is not None:
        block.block_type = payload.block_type
        block.is_user_edited = True
    if payload.section_id is not None:
        sec_res = await db.execute(
            select(AgendaSection).where(
                AgendaSection.id == payload.section_id,
                AgendaSection.session_id == block.session_id,
            )
        )
        if sec_res.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
            )
        block.section_id = payload.section_id
        block.is_user_edited = True
    if payload.assignee is not None:
        block.assignee = payload.assignee.strip() or None
        block.is_user_edited = True
    if payload.due_date_text is not None:
        block.due_date_text = payload.due_date_text.strip() or None
        block.is_user_edited = True
    if payload.completed is not None:
        block.completed = payload.completed
        block.is_user_edited = True

    # Keep linked ActionItem synchronized
    ai_res = await db.execute(
        select(ActionItem).where(ActionItem.block_id == block.id)
    )
    linked_action = ai_res.scalar_one_or_none()
    if block.block_type == "action_item":
        if linked_action is None:
            linked_action = ActionItem(
                session_id=block.session_id,
                block_id=block.id,
                description=block.content,
                assignee=block.assignee,
                due_date_text=block.due_date_text,
                completed=block.completed,
                source_segment_ids=list(block.source_segment_ids or []),
            )
            db.add(linked_action)
        else:
            linked_action.description = block.content
            linked_action.assignee = block.assignee
            linked_action.due_date_text = block.due_date_text
            linked_action.completed = block.completed
    elif linked_action is not None:
        await db.delete(linked_action)

    await db.commit()
    await db.refresh(block)
    block_dto = serialize_block(block)
    await broker.publish_session_event(
        block.session_id,
        BlockUpdatedEvent(
            session_id=block.session_id,
            action="updated",
            block=block_dto,
            block_id=block.id,
        ).model_dump(),
    )
    return block_dto


@router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_block(
    block_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(NoteBlock)
        .join(Session, NoteBlock.session_id == Session.id)
        .where(NoteBlock.id == block_id, Session.user_id == current_user.id)
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note block not found"
        )
    session_id = block.session_id
    await db.delete(block)
    await db.commit()
    await broker.publish_session_event(
        session_id,
        BlockUpdatedEvent(
            session_id=session_id,
            action="deleted",
            block=None,
            block_id=block_id,
        ).model_dump(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/sections/{section_id}", response_model=AgendaSectionPayload)
async def update_section(
    section_id: str,
    payload: SectionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgendaSectionPayload:
    result = await db.execute(
        select(AgendaSection)
        .join(Session, AgendaSection.session_id == Session.id)
        .where(AgendaSection.id == section_id, Session.user_id == current_user.id)
    )
    section = result.scalar_one_or_none()
    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
        )
    section.title = payload.title.strip()
    section.is_user_edited = True
    await db.commit()
    await db.refresh(section)
    section_dto = serialize_section(section)
    await broker.publish_session_event(
        section.session_id,
        SectionUpdatedEvent(
            session_id=section.session_id,
            section=section_dto,
        ).model_dump(),
    )
    return section_dto


@router.patch("/action-items/{action_id}", response_model=ActionItemPayload)
async def update_action_item(
    action_id: str,
    payload: ActionItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionItemPayload:
    result = await db.execute(
        select(ActionItem)
        .join(Session, ActionItem.session_id == Session.id)
        .where(ActionItem.id == action_id, Session.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found"
        )

    if payload.description is not None:
        item.description = payload.description.strip()
    if payload.assignee is not None:
        item.assignee = payload.assignee.strip() or None
    if payload.due_date_text is not None:
        item.due_date_text = payload.due_date_text.strip() or None
    if payload.completed is not None:
        item.completed = payload.completed

    if item.block_id:
        block_res = await db.execute(
            select(NoteBlock).where(NoteBlock.id == item.block_id)
        )
        block = block_res.scalar_one_or_none()
        if block is not None:
            block.content = item.description
            block.assignee = item.assignee
            block.due_date_text = item.due_date_text
            block.completed = item.completed
            block.is_user_edited = True
            await db.flush()
            await broker.publish_session_event(
                block.session_id,
                BlockUpdatedEvent(
                    session_id=block.session_id,
                    action="updated",
                    block=serialize_block(block),
                    block_id=block.id,
                ).model_dump(),
            )

    await db.commit()
    await db.refresh(item)
    return ActionItemPayload(
        id=item.id,
        session_id=item.session_id,
        block_id=item.block_id,
        description=item.description,
        assignee=item.assignee,
        due_date_text=item.due_date_text,
        completed=item.completed,
        source_segment_ids=list(item.source_segment_ids or []),
    )
