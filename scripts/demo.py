"""End-to-end demo proving cross-instance fan-out via Redis.

Run the stack first (`make up`), then `make demo` (or `uv run python scripts/demo.py`).

Flow: sign up -> create conversation -> open a socket on api1 AND api2 -> send a
message on api1 -> watch BOTH sockets receive the user echo + the mock assistant
reply -> reconnect on api2 -> fetch history over REST.

api1 is published on :8001 and api2 on :8002 by docker-compose; a message sent to one
replica reaching a socket on the other is the whole point of the Redis layer.
"""

import asyncio
import json
import uuid

import httpx
import websockets

API1 = "http://localhost:8001"
API2 = "http://localhost:8002"
WS1 = "ws://localhost:8001"
WS2 = "ws://localhost:8002"


def log(step: str, detail: str = "") -> None:
    print(f"\033[36m[{step}]\033[0m {detail}")


async def recv_message_frame(ws: websockets.WebSocketClientProtocol) -> dict:
    """Read until a 'message' frame arrives (skip auth_ok/pong)."""
    while True:
        frame = json.loads(await ws.recv())
        if frame.get("type") == "message":
            return frame


async def open_authed_socket(
    ws_base: str, conversation_id: str, token: str
) -> websockets.WebSocketClientProtocol:
    ws = await websockets.connect(f"{ws_base}/ws/conversations/{conversation_id}")
    await ws.send(json.dumps({"type": "auth", "token": token}))
    ack = json.loads(await ws.recv())
    assert ack["type"] == "auth_ok", ack
    return ws


async def main() -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
        r = await http.post(
            f"{API1}/auth/signup", json={"email": email, "password": "supersecret1"}
        )
        r.raise_for_status()
        tokens = r.json()
        access = tokens["access_token"]
        log("signup", email)

        headers = {"Authorization": f"Bearer {access}"}
        r = await http.post(
            f"{API1}/conversations", json={"title": "Demo"}, headers=headers
        )
        r.raise_for_status()
        cid = r.json()["id"]
        log("conversation", cid)

        # Two sockets, two different replicas, same conversation.
        ws_a = await open_authed_socket(WS1, cid, access)
        ws_b = await open_authed_socket(WS2, cid, access)
        log("connected", "socket A -> api1:8001, socket B -> api2:8002")

        # Send on A; expect user echo + assistant reply on BOTH.
        await ws_a.send(
            json.dumps(
                {
                    "type": "message",
                    "content": "Hello across replicas!",
                    "client_id": uuid.uuid4().hex,
                }
            )
        )
        log("sent", "message on socket A (api1)")

        for label, ws in (("A/api1", ws_a), ("B/api2", ws_b)):
            user_frame = await asyncio.wait_for(recv_message_frame(ws), timeout=5)
            assistant_frame = await asyncio.wait_for(recv_message_frame(ws), timeout=5)
            log(
                f"received on {label}",
                f"user={user_frame['message']['content']!r} "
                f"assistant={assistant_frame['message']['content']!r}",
            )

        await ws_a.close()
        await ws_b.close()
        log("reconnect", "closed both; fetching history over REST")

        r = await http.get(f"{API2}/conversations/{cid}/messages", headers=headers)
        r.raise_for_status()
        msgs = r.json()["messages"]
        log("history", f"{len(msgs)} persisted messages survive reconnect")
        for m in reversed(msgs):
            print(f"    {m['role']:>9}: {m['content']}")

    print("\n\033[32m✓ cross-instance fan-out + persistence verified\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
