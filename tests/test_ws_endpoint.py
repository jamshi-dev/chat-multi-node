"""End-to-end tests of the real /ws/conversations/{id} route.

These drive the actual WebSocket transport via Starlette's TestClient: the auth
handshake, close codes, and the persist -> broadcast -> assistant-reply path over a
live socket. The TestClient runs the app's lifespan, so the Broadcaster starts against
the shared fakeredis and messages fan out through Redis exactly as in production.
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app


@pytest.fixture
def ws_client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as client:  # __enter__ runs lifespan (starts broadcaster)
        yield client


def _register(client: TestClient) -> str:
    r = client.post(
        "/auth/signup",
        json={"email": f"{uuid.uuid4().hex}@example.com", "password": "supersecret1"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _create_conversation(client: TestClient, token: str) -> str:
    r = client.post(
        "/conversations",
        json={"title": "t"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ws_happy_path_send_broadcast_and_assistant_reply(ws_client: TestClient) -> None:
    token = _register(ws_client)
    cid = _create_conversation(ws_client, token)

    with ws_client.websocket_connect(f"/ws/conversations/{cid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_json({"type": "message", "content": "hello over the socket"})
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "message" and second["type"] == "message"
    assert {first["message"]["role"], second["message"]["role"]} == {"user", "assistant"}
    assert first["message"]["content"] == "hello over the socket"


def test_ws_rejects_invalid_token(ws_client: TestClient) -> None:
    cid = uuid.uuid4()
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(f"/ws/conversations/{cid}") as ws:
            ws.send_json({"type": "auth", "token": "garbage"})
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_rejects_when_first_frame_is_not_auth(ws_client: TestClient) -> None:
    cid = uuid.uuid4()
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(f"/ws/conversations/{cid}") as ws:
            ws.send_json({"type": "message", "content": "hi"})
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_forbids_non_owner(ws_client: TestClient) -> None:
    owner_token = _register(ws_client)
    cid = _create_conversation(ws_client, owner_token)

    other_token = _register(ws_client)  # authenticates fine, but doesn't own the conv
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(f"/ws/conversations/{cid}") as ws:
            ws.send_json({"type": "auth", "token": other_token})
            ws.receive_json()
    assert exc.value.code == 4403


def test_ws_unknown_conversation_closes_4404(ws_client: TestClient) -> None:
    token = _register(ws_client)
    missing = uuid.uuid4()
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(f"/ws/conversations/{missing}") as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.receive_json()
    assert exc.value.code == 4404


def test_ws_ping_pong(ws_client: TestClient) -> None:
    token = _register(ws_client)
    cid = _create_conversation(ws_client, token)
    with ws_client.websocket_connect(f"/ws/conversations/{cid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_bad_frame_returns_error_not_disconnect(ws_client: TestClient) -> None:
    token = _register(ws_client)
    cid = _create_conversation(ws_client, token)
    with ws_client.websocket_connect(f"/ws/conversations/{cid}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "auth_ok"

        # Unknown frame type: server replies with an error frame and stays open.
        ws.send_json({"type": "nonsense"})
        reply = ws.receive_json()
        assert reply["type"] == "error"

        # Still usable afterwards.
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
