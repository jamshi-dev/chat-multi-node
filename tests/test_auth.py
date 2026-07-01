import uuid

from httpx import AsyncClient

from app.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    h = hash_password("supersecret1")
    assert h != "supersecret1"  # never store plaintext
    assert verify_password("supersecret1", h)
    assert not verify_password("wrong", h)


def test_token_type_is_enforced() -> None:
    uid = uuid.uuid4()
    access = create_token(uid, "access")
    assert decode_token(access, "access") == uid
    # An access token must not be accepted where a refresh token is expected.
    try:
        decode_token(access, "refresh")
        raise AssertionError("expected TokenError")
    except TokenError:
        pass


async def test_signup_login_refresh_me(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/signup", json={"email": "bob@example.com", "password": "hunter2hunter"}
    )
    assert r.status_code == 201
    tokens = r.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    r = await client.post(
        "/auth/login", json={"email": "bob@example.com", "password": "hunter2hunter"}
    )
    assert r.status_code == 200

    r = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 200
    assert r.json()["access_token"]

    r = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert r.status_code == 200
    assert r.json()["email"] == "bob@example.com"


async def test_duplicate_email_rejected(client: AsyncClient) -> None:
    body = {"email": "dup@example.com", "password": "password123"}
    assert (await client.post("/auth/signup", json=body)).status_code == 201
    assert (await client.post("/auth/signup", json=body)).status_code == 409


async def test_login_bad_password(client: AsyncClient) -> None:
    await client.post(
        "/auth/signup", json={"email": "c@example.com", "password": "password123"}
    )
    r = await client.post(
        "/auth/login", json={"email": "c@example.com", "password": "nope"}
    )
    assert r.status_code == 401


async def test_protected_route_requires_token(client: AsyncClient) -> None:
    assert (await client.get("/conversations")).status_code in (401, 403)
