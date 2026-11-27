from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_user_from_token(token: str, db: AsyncSession) -> User | None:
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        return None
    user_id = str(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    token_query: str | None = Query(default=None, alias="token"),
    db: AsyncSession = Depends(get_db),
) -> User:
    raw_token = credentials.credentials if credentials else token_query
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    user = await _resolve_user_from_token(raw_token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
        )
    return user


async def get_current_user_ws(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    return await _resolve_user_from_token(token, db)
