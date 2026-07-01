# dizzaract — Real-Time AI Chat Backend

A small, horizontally-scalable real-time chat backend. Authenticated users open a
WebSocket per conversation, send messages, and get a (mock) AI assistant reply.
Messages are persisted to Postgres and fanned out to every connected client —
**across multiple server instances** — via Redis pub/sub.

**Stack:** FastAPI · Python 3.12 · WebSockets · PostgreSQL (asyncpg) · Redis pub/sub ·
SQLAlchemy 2.0 (async) · Alembic · Pydantic v2. Tooling: `uv`, `ruff`, `ty`, `pytest`.

> The AI is intentionally a mock (canned/echoed reply). See `app/services/ai.py`.
> Design rationale and self-critique live in [NOTES.md](NOTES.md).

---

## Quick start (Docker)

```bash
cp .env.example .env          # tweak JWT_SECRET etc. (defaults work for local)
make up                       # builds + starts Postgres, Redis, migrate, api1, api2
```

`make up` brings up, on a clean checkout:

- **Postgres** and **Redis**
- a one-shot **migrate** service (`alembic upgrade head`) that the API waits on
- **two API replicas** — `api1` on http://localhost:8001 and `api2` on http://localhost:8002

Migrations run automatically. Health check: `curl localhost:8001/health`.
OpenAPI docs: http://localhost:8001/docs.

### See it work (cross-instance fan-out)

In another terminal, with the stack up:

```bash
make demo
```

This signs up, creates a conversation, opens a socket on **api1** and another on
**api2**, sends one message to api1, and shows **both** sockets receiving the user echo
and the mock assistant reply — proving the Redis fan-out works across replicas — then
fetches persisted history over REST. (`scripts/demo.py`.)

---

## Local development (no Docker for the app)

Requires Postgres + Redis reachable (e.g. `docker compose up postgres redis`).

```bash
make install                  # uv sync (app + dev deps)
# point DATABASE_URL/REDIS_URL at localhost in your .env, then:
uv run alembic upgrade head
make dev                      # uvicorn with autoreload on :8000
```

## Make targets

```
make help        # list everything
make check       # lint + typecheck + test (the CI gate)
make test        # pytest
make lint        # ruff check
make typecheck   # ty check
make fmt         # ruff format + --fix
make up / down   # docker stack up / down (-v)
make logs        # tail api1 + api2
make migrate     # run migrations against the stack
make revision m="msg"   # autogenerate a migration
make demo        # end-to-end cross-instance demo
```

---

## API surface

REST (auth + CRUD + history), WebSocket (live send/broadcast). Why the split:
WebSocket is the live path; REST is request/response work that doesn't need a socket.
Full reasoning in [NOTES.md](NOTES.md).

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/auth/signup` | → access + refresh tokens |
| POST | `/auth/login` | email/password → tokens |
| POST | `/auth/refresh` | refresh token → new pair |
| GET | `/auth/me` | current user (bearer access token) |
| POST | `/conversations` | create |
| GET | `/conversations` | list (owner's, non-deleted) |
| PATCH | `/conversations/{id}` | rename |
| DELETE | `/conversations/{id}` | soft-delete |
| POST | `/conversations/{id}/restore` | undo soft-delete |
| GET | `/conversations/{id}/messages` | history, cursor-paginated (`limit`, `before`) |
| WS | `/ws/conversations/{id}` | live messaging |
| GET | `/health` | liveness |

### WebSocket protocol

Authenticate by sending an **auth frame as the first message** (rationale in NOTES):

```jsonc
// client -> server
{"type": "auth", "token": "<access JWT>"}
{"type": "message", "content": "hello", "client_id": "<uuid, optional idempotency key>"}
{"type": "ping"}

// server -> client
{"type": "auth_ok", "user_id": "..."}
{"type": "message", "message": { "id", "conversation_id", "role", "sender_id", "content", "client_id", "created_at" }}
{"type": "pong"}
{"type": "error", "code": "...", "message": "..."}
```

Close codes: `4401` unauthenticated/expired · `4403` not your conversation ·
`4404` no such conversation · `4408` auth frame timeout · `1001` server shutting down.

### Manual websocat sequence

```bash
# 1. get a token
TOKEN=$(curl -s localhost:8001/auth/signup \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"supersecret1"}' | jq -r .access_token)

# 2. create a conversation
CID=$(curl -s localhost:8001/conversations -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"title":"hi"}' | jq -r .id)

# 3. open a socket, authenticate, send a message
websocat ws://localhost:8001/ws/conversations/$CID
{"type":"auth","token":"<paste $TOKEN>"}
{"type":"message","content":"hello there"}
# -> you receive the user message echo and the assistant reply

# 4. history survives reconnect
curl -s localhost:8001/conversations/$CID/messages -H "authorization: Bearer $TOKEN" | jq
```

---

## Tests

```bash
make test
```

Covered: password hashing & JWT type enforcement; auth + conversation REST flows;
ownership isolation; **cross-instance Redis fan-out** (`tests/test_broadcaster.py`);
send→persist→broadcast→assistant reply (`tests/test_chat_flow.py`); client-id dedupe;
cursor pagination; and the **live WebSocket endpoint** end to end
(`tests/test_ws_endpoint.py`) — auth handshake, `auth_ok`, message broadcast +
assistant reply over a real socket, ping/pong, bad-frame handling, and every close
code (`4401`/`4403`/`4404`). Tests use SQLite + an in-process fakeredis, so `make test`
needs no running services.

## What's not done / cut

See [NOTES.md](NOTES.md) for the honest list. Short version: token streaming, presence/
typing, Prometheus metrics, OAuth, and a refresh-token denylist are intentionally out
of scope. None of the four "must build" sections were cut.
