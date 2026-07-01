import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, owner_id: uuid.UUID, title: str) -> Conversation:
        conv = Conversation(owner_id=owner_id, title=title)
        self.session.add(conv)
        await self.session.flush()
        return conv

    async def get(
        self, conversation_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Conversation | None:
        conv = await self.session.get(Conversation, conversation_id)
        if conv is None:
            return None
        if conv.deleted_at is not None and not include_deleted:
            return None
        return conv

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Conversation]:
        result = await self.session.execute(
            select(Conversation)
            .where(
                Conversation.owner_id == owner_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.created_at.desc())
        )
        return list(result.scalars().all())
