"""Real-time fan-out across instances via Redis pub/sub.

Design
------
Each app instance keeps an in-process registry of the WebSocket connections it is
serving, keyed by conversation id. To make a message reach clients on *other*
instances, we never deliver in-process directly: the sender ``publish()``es to a
Redis channel, and every instance's reader task picks it up and delivers to its own
local sockets. The publishing instance receives its own message back the same way,
so there is exactly one delivery path — no double-sends, no "did this come from me?"
bookkeeping.

We ``psubscribe`` once to ``chat:conv:*`` rather than subscribing per conversation.
That trades a little extra cross-instance traffic (each instance sees every
conversation's frames) for a much simpler, race-free subscription model. At larger
scale you'd switch to refcounted per-conversation subscriptions — noted in NOTES.md.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

import redis.asyncio as aioredis
from fastapi import WebSocket

logger = logging.getLogger("app.broadcaster")

_CHANNEL_PREFIX = "chat:conv:"
_PATTERN = f"{_CHANNEL_PREFIX}*"


def _channel(conversation_id: str) -> str:
    return f"{_CHANNEL_PREFIX}{conversation_id}"


@dataclass(eq=False)  # identity-based hashing so connections can live in a set
class Connection:
    """A live WebSocket plus a send lock so only one frame is written at a time."""

    websocket: WebSocket
    user_id: str
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, data: dict) -> None:
        async with self._lock:
            await self.websocket.send_json(data)


class Broadcaster:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._local: dict[str, set[Connection]] = {}
        self._running = False

    # ---- lifecycle ----

    async def start(self) -> None:
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        await self._redis.ping()
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.psubscribe(_PATTERN)
        self._running = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info("broadcaster started", extra={"pattern": _PATTERN})

    async def stop(self) -> None:
        self._running = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            await self._pubsub.punsubscribe(_PATTERN)
            await self._pubsub.aclose()
        if self._redis is not None:
            await self._redis.aclose()
        logger.info("broadcaster stopped")

    # ---- local registry ----

    def add(self, conversation_id: str, conn: Connection) -> None:
        self._local.setdefault(conversation_id, set()).add(conn)

    def remove(self, conversation_id: str, conn: Connection) -> None:
        conns = self._local.get(conversation_id)
        if conns is not None:
            conns.discard(conn)
            if not conns:
                self._local.pop(conversation_id, None)

    def local_connection_count(self, conversation_id: str) -> int:
        return len(self._local.get(conversation_id, ()))

    async def close_all(self, code: int = 1001) -> None:
        """Drain every local socket — used on graceful shutdown."""
        for conns in list(self._local.values()):
            for conn in list(conns):
                try:
                    await conn.websocket.close(code=code)
                except Exception:  # noqa: BLE001 - best effort during shutdown
                    pass
        self._local.clear()

    # ---- publish / deliver ----

    async def publish(self, conversation_id: str, payload: dict) -> None:
        if self._redis is None:
            raise RuntimeError("broadcaster not started")
        await self._redis.publish(_channel(conversation_id), json.dumps(payload))

    async def _deliver_local(self, conversation_id: str, payload: dict) -> None:
        dead: list[Connection] = []
        for conn in list(self._local.get(conversation_id, ())):
            try:
                await conn.send(payload)
            except Exception:  # noqa: BLE001 - a dead socket must not stop fan-out
                dead.append(conn)
        for conn in dead:
            self.remove(conversation_id, conn)

    async def _reader_loop(self) -> None:
        assert self._pubsub is not None
        while self._running:
            try:
                message = await self._pubsub.get_message(timeout=1.0)
                if message is None or message.get("type") != "pmessage":
                    continue
                channel: str = message["channel"]
                conversation_id = channel.removeprefix(_CHANNEL_PREFIX)
                payload = json.loads(message["data"])
                await self._deliver_local(conversation_id, payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the reader die on one bad frame
                logger.exception("broadcaster reader error")
                await asyncio.sleep(0.5)
