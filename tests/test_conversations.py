from httpx import AsyncClient


async def test_conversation_crud(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    # create
    r = await client.post("/conversations", json={"title": "First"}, headers=auth_headers)
    assert r.status_code == 201
    conv = r.json()
    assert conv["title"] == "First"
    cid = conv["id"]

    # list
    r = await client.get("/conversations", headers=auth_headers)
    assert r.status_code == 200
    assert [c["id"] for c in r.json()] == [cid]

    # rename
    r = await client.patch(
        f"/conversations/{cid}", json={"title": "Renamed"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"

    # delete (soft) then it disappears from list
    r = await client.delete(f"/conversations/{cid}", headers=auth_headers)
    assert r.status_code == 204
    r = await client.get("/conversations", headers=auth_headers)
    assert r.json() == []

    # restore brings it back
    r = await client.post(f"/conversations/{cid}/restore", headers=auth_headers)
    assert r.status_code == 200
    r = await client.get("/conversations", headers=auth_headers)
    assert len(r.json()) == 1


async def test_cannot_access_others_conversation(client: AsyncClient) -> None:
    # alice creates a conversation
    a = (
        await client.post(
            "/auth/signup", json={"email": "a@x.com", "password": "password123"}
        )
    ).json()
    a_headers = {"Authorization": f"Bearer {a['access_token']}"}
    cid = (
        await client.post("/conversations", json={"title": "secret"}, headers=a_headers)
    ).json()["id"]

    # mallory cannot see or touch it (404 to hide existence)
    m = (
        await client.post(
            "/auth/signup", json={"email": "m@x.com", "password": "password123"}
        )
    ).json()
    m_headers = {"Authorization": f"Bearer {m['access_token']}"}
    assert (
        await client.get(f"/conversations/{cid}/messages", headers=m_headers)
    ).status_code == 404
    assert (
        await client.patch(
            f"/conversations/{cid}", json={"title": "hijack"}, headers=m_headers
        )
    ).status_code == 404


async def test_validation_error_is_structured(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post("/conversations", json={"title": ""}, headers=auth_headers)
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error"
    assert isinstance(body["detail"], list)
