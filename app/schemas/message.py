from datetime import datetime

from pydantic import BaseModel

from app.models.message import MessageRole


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    sender_id: str | None
    role: MessageRole
    content: str
    client_id: str | None
    created_at: datetime


class MessagePage(BaseModel):
    """Cursor-paginated history. `next_cursor` is the id to pass as `before` for older messages."""

    messages: list[MessageOut]
    next_cursor: str | None
