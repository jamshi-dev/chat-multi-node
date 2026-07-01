"""Chat use-case: persist a user message, then persist + broadcast a mock AI reply.

Each step runs in its own DB transaction so a later failure (e.g. the AI step) never
rolls back an already-acknowledged user message. Broadcasting happens only after the
row is committed, so what clients see always matches what's durable.
"""

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.message import Message, MessageRole
from app.repositories.message import MessageRepository
from app.schemas.message import MessageOut
from app.schemas.ws import message_event
from app.services import ai
from app.services.broadcaster import Broadcaster

logger = logging.getLogger("app.chat")


def _to_out(msg: Message) -> MessageOut:
    return MessageOut(
        id=str(msg.id),
        conversation_id=str(msg.conversation_id),
        sender_id=str(msg.sender_id) if msg.sender_id else None,
        role=msg.role,
        content=msg.content,
        client_id=msg.client_id,
        created_at=msg.created_at,
    )


class ChatService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        broadcaster: Broadcaster,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._broadcaster = broadcaster

    async def handle_user_message(
        self,
        *,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID,
        content: str,
        client_id: str | None,
    ) -> None:
        user_msg = await self._persist_user_message(
            conversation_id, sender_id, content, client_id
        )
        if user_msg is None:
            return  # duplicate client_id — already delivered, idempotent no-op
        await self._broadcaster.publish(
            str(conversation_id), message_event(_to_out(user_msg))
        )

        # AI failure must not crash the socket: persist+broadcast best-effort, log on error.
        try:
            reply_text = await ai.generate_reply(content)
            assistant_msg = await self._persist_assistant_message(
                conversation_id, reply_text
            )
            await self._broadcaster.publish(
                str(conversation_id), message_event(_to_out(assistant_msg))
            )
        except Exception:
            logger.exception(
                "assistant reply failed", extra={"conversation_id": str(conversation_id)}
            )

    async def _persist_user_message(
        self,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID,
        content: str,
        client_id: str | None,
    ) -> Message | None:
        async with self._sessionmaker() as session:
            repo = MessageRepository(session)
            if client_id is not None:
                existing = await repo.get_by_client_id(conversation_id, client_id)
                if existing is not None:
                    return None
            try:
                msg = await repo.create(
                    conversation_id=conversation_id,
                    role=MessageRole.user,
                    content=content,
                    sender_id=sender_id,
                    client_id=client_id,
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None  # lost the dedupe race; the winner already broadcast it
            session.expunge(msg)
            return msg

    async def _persist_assistant_message(
        self, conversation_id: uuid.UUID, content: str
    ) -> Message:
        async with self._sessionmaker() as session:
            repo = MessageRepository(session)
            msg = await repo.create(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=content,
            )
            await session.commit()
            session.expunge(msg)
            return msg
