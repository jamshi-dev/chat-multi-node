"""WebSocket JSON message protocol.

Every frame is a JSON object with a ``type`` discriminator. Incoming frames are
validated with Pydantic; malformed frames get an ``error`` reply rather than a
dropped connection.

Client -> Server:
  {"type": "auth", "token": "<access jwt>"}            # required first frame
  {"type": "message", "content": "...", "client_id": "<uuid>"}
  {"type": "ping"}

Server -> Client:
  {"type": "auth_ok", "user_id": "..."}
  {"type": "message", "message": {...MessageOut...}}   # user + assistant messages
  {"type": "pong"}
  {"type": "error", "code": "...", "message": "..."}
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.message import MessageOut


class AuthFrame(BaseModel):
    type: Literal["auth"]
    token: str


class MessageFrame(BaseModel):
    type: Literal["message"]
    content: str = Field(min_length=1, max_length=8000)
    # Client-supplied idempotency key; enables dedupe + reconnect-safe redelivery.
    client_id: str | None = Field(default=None, max_length=64)


class PingFrame(BaseModel):
    type: Literal["ping"]


IncomingFrame = Annotated[
    AuthFrame | MessageFrame | PingFrame,
    Field(discriminator="type"),
]


# ---- Outgoing helpers (kept as plain dicts for cheap fan-out over Redis) ----


def auth_ok(user_id: str) -> dict:
    return {"type": "auth_ok", "user_id": user_id}


def error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}


def message_event(message: MessageOut) -> dict:
    return {"type": "message", "message": message.model_dump(mode="json")}


def pong() -> dict:
    return {"type": "pong"}
