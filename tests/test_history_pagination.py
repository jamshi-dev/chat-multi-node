"""Cursor-based pagination over message history."""

import uuid

from httpx import AsyncClient

from app.db import get_sessionmaker
from app.models.message import MessageRole
from app.repositories.message import MessageRepository


async def test_cursor_pagination(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    cid = (
        await client.post("/conversations", json={"title": "t"}, headers=auth_headers)
    ).json()["id"]

    # Seed 5 messages directly.
    async with get_sessionmaker()() as session:
        repo = MessageRepository(session)
        for i in range(5):
            await repo.create(
                conversation_id=uuid.UUID(cid),
                role=MessageRole.user,
                content=f"msg-{i}",
            )
        await session.commit()

    # First page (newest 2).
    r = await client.get(
        f"/conversations/{cid}/messages?limit=2", headers=auth_headers
    )
    page1 = r.json()
    assert len(page1["messages"]) == 2
    assert page1["messages"][0]["content"] == "msg-4"
    assert page1["next_cursor"] is not None

    # Second page using the cursor.
    r = await client.get(
        f"/conversations/{cid}/messages?limit=2&before={page1['next_cursor']}",
        headers=auth_headers,
    )
    page2 = r.json()
    assert [m["content"] for m in page2["messages"]] == ["msg-2", "msg-1"]
