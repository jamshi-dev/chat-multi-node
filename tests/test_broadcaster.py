"""The strong signal: a message published on one Broadcaster (one app instance)
reaches local sockets on a *different* Broadcaster, via Redis pub/sub.
"""

import asyncio

from app.services.broadcaster import Broadcaster, Connection


class FakeWebSocket:
    def __init__(self) -> None:
        self.received: list[dict] = []
        self.closed_code: int | None = None

    async def send_json(self, data: dict) -> None:
        self.received.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


async def _wait_for(predicate, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


async def test_fanout_across_two_instances() -> None:
    instance_a = Broadcaster("redis://localhost:6379/0")
    instance_b = Broadcaster("redis://localhost:6379/0")
    await instance_a.start()
    await instance_b.start()
    try:
        conv = "11111111-1111-1111-1111-111111111111"

        ws_a = FakeWebSocket()
        ws_b = FakeWebSocket()
        instance_a.add(conv, Connection(websocket=ws_a, user_id="a"))  # type: ignore[arg-type]
        instance_b.add(conv, Connection(websocket=ws_b, user_id="b"))  # type: ignore[arg-type]

        # Publish from instance A only.
        await instance_a.publish(conv, {"type": "message", "message": {"content": "hi"}})

        # Both instances' local sockets must receive it.
        await _wait_for(lambda: ws_a.received and ws_b.received)
        assert ws_a.received[0]["message"]["content"] == "hi"
        assert ws_b.received[0]["message"]["content"] == "hi"
    finally:
        await instance_a.stop()
        await instance_b.stop()


async def test_message_only_goes_to_subscribed_conversation() -> None:
    instance = Broadcaster("redis://localhost:6379/0")
    await instance.start()
    try:
        ws = FakeWebSocket()
        instance.add("conv-A", Connection(websocket=ws, user_id="u"))  # type: ignore[arg-type]

        # Publish to a different conversation; the socket must not receive it.
        await instance.publish("conv-B", {"type": "message", "message": {"x": 1}})
        await asyncio.sleep(0.2)
        assert ws.received == []
    finally:
        await instance.stop()


async def test_dead_socket_is_pruned_without_blocking_others() -> None:
    instance = Broadcaster("redis://localhost:6379/0")
    await instance.start()
    try:
        good = FakeWebSocket()

        class Boom(FakeWebSocket):
            async def send_json(self, data: dict) -> None:
                raise RuntimeError("socket is dead")

        bad = Boom()
        instance.add("c", Connection(websocket=bad, user_id="bad"))  # type: ignore[arg-type]
        instance.add("c", Connection(websocket=good, user_id="good"))  # type: ignore[arg-type]

        await instance.publish("c", {"type": "message", "message": {"ok": True}})
        await _wait_for(lambda: bool(good.received))
        # The dead connection was removed; the good one still got the message.
        assert instance.local_connection_count("c") == 1
    finally:
        await instance.stop()
