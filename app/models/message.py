import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # History fetch is "newest within a conversation"; this index serves it directly.
        Index("ix_messages_conv_created", "conversation_id", "created_at"),
        # Dedupe of client-supplied ids is scoped per conversation.
        Index("uq_messages_conv_client_id", "conversation_id", "client_id", unique=True),
    )

    # UUIDv7 is time-ordered: ordering by id alone yields chronological order and gives
    # a stable, monotonic pagination cursor — no (timestamp, tiebreaker) needed.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # Null for assistant messages; set to the author for user messages.
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    role: Mapped[MessageRole] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Client-supplied id (idempotency key). Nullable so server-authored rows can omit it.
    client_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
