import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.models.conversation import Conversation
from app.repositories.conversation import ConversationRepository
from app.repositories.message import MessageRepository
from app.schemas.conversation import (
    ConversationCreate,
    ConversationOut,
    ConversationRename,
)
from app.schemas.message import MessageOut, MessagePage

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _out(conv: Conversation) -> ConversationOut:
    return ConversationOut(
        id=str(conv.id),
        title=conv.title,
        owner_id=str(conv.owner_id),
        created_at=conv.created_at,
    )


async def _get_owned(
    session: SessionDep, conversation_id: uuid.UUID, user: CurrentUser
) -> Conversation:
    conv = await ConversationRepository(session).get(conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    if conv.owner_id != user.id:
        # Hide existence from non-owners.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return conv


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, session: SessionDep, user: CurrentUser
) -> ConversationOut:
    conv = await ConversationRepository(session).create(user.id, body.title)
    return _out(conv)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    session: SessionDep, user: CurrentUser
) -> list[ConversationOut]:
    convs = await ConversationRepository(session).list_for_owner(user.id)
    return [_out(c) for c in convs]


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationRename,
    session: SessionDep,
    user: CurrentUser,
) -> ConversationOut:
    conv = await _get_owned(session, conversation_id, user)
    conv.title = body.title
    return _out(conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> None:
    conv = await _get_owned(session, conversation_id, user)
    # Soft delete (NICE TO HAVE) — recoverable via the restore endpoint.
    conv.deleted_at = datetime.now(UTC)


@router.post("/{conversation_id}/restore", response_model=ConversationOut)
async def restore_conversation(
    conversation_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> ConversationOut:
    conv = await ConversationRepository(session).get(
        conversation_id, include_deleted=True
    )
    if conv is None or conv.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    conv.deleted_at = None
    return _out(conv)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def get_history(
    conversation_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[uuid.UUID | None, Query(description="cursor: id to page before")] = None,
) -> MessagePage:
    await _get_owned(session, conversation_id, user)
    rows = await MessageRepository(session).history(
        conversation_id, limit=limit, before=before
    )
    messages = [
        MessageOut(
            id=str(m.id),
            conversation_id=str(m.conversation_id),
            sender_id=str(m.sender_id) if m.sender_id else None,
            role=m.role,
            content=m.content,
            client_id=m.client_id,
            created_at=m.created_at,
        )
        for m in rows
    ]
    next_cursor = messages[-1].id if len(messages) == limit else None
    return MessagePage(messages=messages, next_cursor=next_cursor)
