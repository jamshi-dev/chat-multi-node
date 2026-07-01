"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.user import User
from app.repositories.user import UserRepository
from app.security import TokenError, decode_token
from app.services.broadcaster import Broadcaster
from app.services.chat import ChatService

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    session: SessionDep,
) -> User:
    try:
        user_id = decode_token(credentials.credentials, "access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_broadcaster(request: Request) -> Broadcaster:
    return request.app.state.broadcaster


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service
