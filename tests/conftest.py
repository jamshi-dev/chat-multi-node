"""Test fixtures.

DB: a file-backed SQLite database via aiosqlite, so the async pytest loop and any
TestClient threads see the same data. Redis: a single in-process fakeredis server,
shared by every Broadcaster created in a test, which is what makes the cross-instance
fan-out test meaningful.
"""

import os

# Must be set before app modules import their cached settings.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_dizzaract.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-prod-but-at-least-32-bytes-long")
os.environ.setdefault("AI_REPLY_DELAY_SECONDS", "0")

from collections.abc import AsyncGenerator  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.services.broadcaster import Broadcaster  # noqa: E402
from app.services.chat import ChatService  # noqa: E402

_fake_server = fakeredis.aioredis.FakeServer()


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every Broadcaster's redis client to one shared fakeredis server."""

    def _from_url(*_args: object, **kwargs: object) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(
            server=_fake_server, decode_responses=kwargs.get("decode_responses", False)
        )

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)


@pytest.fixture(autouse=True)
async def _prepare_db() -> AsyncGenerator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def broadcaster() -> AsyncGenerator[Broadcaster]:
    b = Broadcaster("redis://localhost:6379/0")
    await b.start()
    yield b
    await b.stop()


@pytest.fixture
async def client(broadcaster: Broadcaster) -> AsyncGenerator[AsyncClient]:
    app = create_app()
    app.state.broadcaster = broadcaster
    app.state.chat_service = ChatService(get_sessionmaker(), broadcaster)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        "/auth/signup", json={"email": "alice@example.com", "password": "supersecret1"}
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
