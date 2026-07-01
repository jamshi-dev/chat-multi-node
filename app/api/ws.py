"""WebSocket endpoint for a single conversation.

Auth model: the socket is accepted, then the client must send an ``auth`` frame as its
first message (carrying the short-lived access JWT) within a timeout. Rationale lives in
NOTES.md — briefly: query-param tokens leak into proxy/access logs and browser history,
and subprotocol smuggling is awkward for clients; an explicit first-frame keeps the
token out of URLs and makes the handshake legible.

Close codes (application range 4xxx):
  4401 unauthenticated / expired token      4403 not a member of the conversation
  4404 conversation not found               4408 auth frame not received in time
  1011 unexpected server error              1001 server going away (graceful shutdown)
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.config import get_settings
from app.db import get_sessionmaker
from app.repositories.conversation import ConversationRepository
from app.repositories.user import UserRepository
from app.schemas.ws import (
    AuthFrame,
    IncomingFrame,
    MessageFrame,
    PingFrame,
    auth_ok,
    error,
    pong,
)
from app.security import TokenError, decode_token
from app.services.broadcaster import Broadcaster, Connection

logger = logging.getLogger("app.ws")
router = APIRouter()

_frame_adapter: TypeAdapter[IncomingFrame] = TypeAdapter(IncomingFrame)


async def _authenticate(websocket: WebSocket) -> uuid.UUID | None:
    """Wait for the first frame, validate it as an auth frame, return the user id."""
    settings = get_settings()
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=settings.ws_auth_timeout_seconds
        )
    except TimeoutError:
        await websocket.close(code=4408, reason="auth timeout")
        return None
    except WebSocketDisconnect:
        return None

    try:
        frame = _frame_adapter.validate_json(raw)
    except ValidationError:
        await websocket.close(code=4401, reason="expected auth frame")
        return None
    if not isinstance(frame, AuthFrame):
        await websocket.close(code=4401, reason="expected auth frame")
        return None

    try:
        return decode_token(frame.token, "access")
    except TokenError:
        await websocket.close(code=4401, reason="invalid or expired token")
        return None


@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_socket(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    broadcaster: Broadcaster = websocket.app.state.broadcaster
    chat_service = websocket.app.state.chat_service
    sessionmaker = get_sessionmaker()

    await websocket.accept()

    user_id = await _authenticate(websocket)
    if user_id is None:
        return

    # Authorize: conversation must exist and belong to the authenticated user.
    async with sessionmaker() as session:
        user = await UserRepository(session).get_by_id(user_id)
        conv = await ConversationRepository(session).get(conversation_id)
    if user is None:
        await websocket.close(code=4401, reason="user not found")
        return
    if conv is None:
        await websocket.close(code=4404, reason="conversation not found")
        return
    if conv.owner_id != user_id:
        await websocket.close(code=4403, reason="forbidden")
        return

    conv_key = str(conversation_id)
    conn = Connection(websocket=websocket, user_id=str(user_id))
    broadcaster.add(conv_key, conn)
    await conn.send(auth_ok(str(user_id)))
    logger.info(
        "ws connected",
        extra={"conversation_id": conv_key, "user_id": str(user_id)},
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = _frame_adapter.validate_json(raw)
            except ValidationError as exc:
                await conn.send(error("bad_frame", exc.errors()[0].get("msg", "invalid frame")))
                continue

            if isinstance(frame, MessageFrame):
                # Persist user msg + broadcast, then mock AI reply + broadcast. Awaited
                # inline so a slow/failed reply applies natural backpressure per socket
                # rather than spawning unbounded background tasks.
                await chat_service.handle_user_message(
                    conversation_id=conversation_id,
                    sender_id=user_id,
                    content=frame.content,
                    client_id=frame.client_id,
                )
            elif isinstance(frame, PingFrame):
                await conn.send(pong())
            elif isinstance(frame, AuthFrame):
                await conn.send(error("already_authenticated", "already authenticated"))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws error", extra={"conversation_id": conv_key})
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
    finally:
        broadcaster.remove(conv_key, conn)
        logger.info(
            "ws disconnected",
            extra={"conversation_id": conv_key, "user_id": str(user_id)},
        )
