import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content: str,
        sender_id: uuid.UUID | None = None,
        client_id: str | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sender_id=sender_id,
            client_id=client_id,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def get_by_client_id(
        self, conversation_id: uuid.UUID, client_id: str
    ) -> Message | None:
        result = await self.session.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_id == client_id,
            )
        )
        return result.scalar_one_or_none()

    async def history(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int = 50,
        before: uuid.UUID | None = None,
    ) -> list[Message]:
        """Return up to ``limit`` messages, newest first.

        Cursor-based: ``before`` is a message id; we return rows strictly older than it.
        Message ids are UUIDv7 (time-ordered), so the id alone is a correct, monotonic
        ordering key and cursor — robust even when many rows share a ``created_at``.
        """
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if before is not None:
            stmt = stmt.where(Message.id < before)
        stmt = stmt.order_by(Message.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
