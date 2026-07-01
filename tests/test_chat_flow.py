"""Send -> persist -> broadcast -> mock assistant reply, end to end through ChatService."""

import asyncio
import uuid

from httpx import AsyncClient

from app.db import get_sessionmaker
from app.services.broadcaster import Broadcaster, Connection
from app.services.chat import ChatService
from tests.test_broadcaster import FakeWebSocket, _wait_for


async def test_send_persists_and_broadcasts_user_and_assistant(
    client: AsyncClient, auth_headers: dict[str, str], broadcaster: Broadcaster
) -> None:
    me = (await client.get("/auth/me", headers=auth_headers)).json()
    user_id = uuid.UUID(me["id"])
    cid = (
        await client.post("/conversations", json={"title": "t"}, headers=auth_headers)
    ).json()["id"]

    # A subscriber for this conversation on this instance.
    ws = FakeWebSocket()
    broadcaster.add(cid, Connection(websocket=ws, user_id=str(user_id)))  # type: ignore[arg-type]

    chat = ChatService(get_sessionmaker(), broadcaster)
    await chat.handle_user_message(
        conversation_id=uuid.UUID(cid),
        sender_id=user_id,
        content="hello there",
        client_id="client-msg-1",
    )

    # Broadcast: both the user echo and the assistant reply arrive.
    await _wait_for(lambda: len(ws.received) >= 2)
    roles = [m["message"]["role"] for m in ws.received]
    assert roles == ["user", "assistant"]

    # Persistence: history endpoint returns both, newest first.
    r = await client.get(f"/conversations/{cid}/messages", headers=auth_headers)
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert {m["role"] for m in msgs} == {"user", "assistant"}
    assert any(m["content"] == "hello there" for m in msgs)


async def test_duplicate_client_id_is_deduped(
    client: AsyncClient, auth_headers: dict[str, str], broadcaster: Broadcaster
) -> None:
    me = (await client.get("/auth/me", headers=auth_headers)).json()
    user_id = uuid.UUID(me["id"])
    cid = (
        await client.post("/conversations", json={"title": "t"}, headers=auth_headers)
    ).json()["id"]

    chat = ChatService(get_sessionmaker(), broadcaster)
    for _ in range(2):
        await chat.handle_user_message(
            conversation_id=uuid.UUID(cid),
            sender_id=user_id,
            content="same message",
            client_id="dup-key",
        )
        await asyncio.sleep(0.05)

    msgs = (
        await client.get(f"/conversations/{cid}/messages", headers=auth_headers)
    ).json()["messages"]
    # Exactly one user row for the repeated client_id (plus its one assistant reply).
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1
