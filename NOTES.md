# NOTES

Design decisions, trade-offs, and an honest account of what was cut.

## Structure

```
app/
  api/          routing layer (auth, conversations, ws) + FastAPI deps
  services/     use-cases: auth, chat (persist+broadcast), ai (mock), broadcaster
  repositories/ data access (one per aggregate)
  models/       SQLAlchemy 2.0 mapped classes
  schemas/      Pydantic v2 models — the validated boundary
  security.py   argon2 hashing + JWT encode/decode
  db.py         async engine/session factory
  broadcaster   the cross-instance fan-out core (services/broadcaster.py)
alembic/        migrations (autogenerate-ready)
tests/          pytest (sqlite + fakeredis, no services needed)
scripts/demo.py end-to-end cross-instance proof
```

Routing → service → repository, with Pydantic at the edges and SQLAlchemy models
never leaking past the service layer. Routes translate domain errors
(`AuthError`, ownership checks) into HTTP/WS status and close codes.

## REST vs WebSocket split

- **WebSocket** owns the live path only: sending a message and receiving the
  broadcast + assistant reply. That's the part that genuinely needs a persistent,
  push-capable connection.
- **REST** owns everything request/response: signup/login/refresh, conversation
  CRUD, and history fetch. These are cacheable, easy to test with curl, and don't
  benefit from a socket. History on join is a `GET …/messages` — the client fetches
  the page it needs rather than the server pushing a backlog over the socket.

## WebSocket authentication: first-message auth frame

The client must send `{"type":"auth","token":"<access JWT>"}` as its first frame
within a timeout (`WS_AUTH_TIMEOUT_SECONDS`, default 5s); otherwise the socket is
closed `4408`. Considered alternatives:

- **Query param** (`?token=…`) — simplest, and lets you reject before `accept()`.
  Rejected because tokens land in proxy/access logs, browser history, and `Referer`
  headers. Leaking a bearer credential into logs is exactly the kind of thing this
  assignment penalizes.
- **`Sec-WebSocket-Protocol` subprotocol smuggling** — keeps it out of the URL, but
  it's a hack (the header isn't meant for credentials) and is awkward for browser
  clients, which can only pass it via the subprotocol negotiation API.
- **First-message frame** (chosen) — token never appears in a URL or handshake
  header, the protocol is explicit and easy to reason about, and the small cost is
  that we `accept()` before authenticating. We bound that cost with the auth timeout.

Close codes: `4401` unauthenticated/expired · `4403` not the owner · `4404` no such
conversation · `4408` auth timeout · `1001` graceful shutdown · `1011` server error.

## Real-time fan-out (the core)

There is exactly **one** delivery path: persist → `publish` to a Redis channel →
every instance's reader picks it up → deliver to that instance's local sockets. The
publishing instance receives its own message back the same way, so there's no
"deliver locally *and* publish" double-send and no origin bookkeeping.

- Subscription model: a single `psubscribe("chat:conv:*")` per instance rather than
  refcounted per-conversation subscribe/unsubscribe. This trades a little extra
  cross-instance traffic (every instance sees every conversation's frames and filters
  by whether it has local sockets) for a race-free, dead-simple model. At real scale
  you'd switch to refcounted per-conversation channels — noted as future work.
- One **sender task** per instance (the reader) writes to sockets, plus a per-socket
  `asyncio.Lock`, so frames never interleave on a single connection.
- Dead sockets are pruned on send failure inside the reader; the `finally` in the WS
  handler also removes on disconnect. Removal is idempotent (set discard).
- `docker-compose` runs **two replicas** (`api1`, `api2`); `make demo` /
  `scripts/demo.py` opens a socket on each and shows a message sent to one arriving
  on the other — verified.

## Data layer

- **SQLAlchemy 2.0 async + asyncpg.** Async throughout, no blocking calls in the
  event loop. Migrations via **Alembic** (async env), applied by a one-shot `migrate`
  compose service before the API starts.
- **Message ids are UUIDv7** (time-ordered). Ordering by `id` alone is chronological
  and gives a stable, monotonic pagination cursor — robust even when many rows share a
  `created_at` (Postgres `now()` is transaction-start time, so rows committed together
  would otherwise tie). History is **cursor-paginated** (`limit` + `before`).
- Per-message transactions: the user message and the assistant reply each commit in
  their own session, and we only broadcast after commit — so what clients see always
  matches what's durable, and a later failure can't roll back an acknowledged message.

## Robustness

- A failed mock-AI call is caught and logged; the user's message is already persisted
  and broadcast, and the socket stays open.
- A bad/malformed WS frame returns an `error` frame instead of dropping the socket.
- Request validation errors return a structured `{"error","detail"}` body (422).
- Graceful shutdown drains sockets (`close(1001)`), stops the Redis reader, and
  disposes the engine — verified in the logs.
- Structured JSON logging via the stdlib (no structlog dependency for a small service).

## Security

- Passwords hashed with **argon2** (argon2-cffi); plaintext never stored.
- Short-lived **access** tokens + long-lived **refresh** tokens; token `type` is
  validated so an access token can't be used to refresh and vice-versa.
- Tokens validated on every privileged REST call and on every socket connect.
- Secrets via env (`JWT_SECRET` etc.); none committed. `.env.example` documents them.
- CORS restricted to a configured origin list.

## Bonus included (small, low-risk)

- **Idempotency / dedupe**: client-supplied `client_id` is a unique key per
  conversation; a repeat is a no-op (covered by a test). This is the building block
  for at-least-once redelivery on reconnect.
- **Soft-delete + restore** for conversations, and **cursor-based** (not offset)
  pagination — both listed as NICE TO HAVE.

## What I cut, and why

- **Streamed/token-by-token assistant replies** over the socket. The protocol and
  single-sender model would support it cleanly (emit `message_delta` frames), but it
  adds surface area for no backend-evaluation benefit. The mock returns a full reply.
- **Typing indicators / presence.** Pure addition; out of the must-build scope.
- **Refresh-token rotation + server-side denylist/revocation.** Current refresh
  tokens are stateless and valid until expiry. Production-grade logout/revocation
  would need a stored jti denylist (Redis). Called out honestly as a gap.
- **Per-user rate limiting**, **Prometheus `/metrics`**, **OAuth**, and a **k6/locust
  load test with numbers** — all bonus; skipped for time. `/health` is implemented.

## Ambiguities / calls I made

- "Fetch history on join" — implemented as a REST `GET …/messages` the client calls,
  rather than the server pushing a backlog frame on connect. Cleaner and paginated.
- Assistant messages have `sender_id = NULL` and `role = assistant`; user messages
  carry the author. `role` distinguishes them without a synthetic AI user row.
- Conversations are single-owner (the creator). The assignment implies multi-client
  fan-out per conversation but not multi-user membership/ACLs, so I kept ownership
  simple; a `conversation_members` table is the natural extension.

## Verified manually

`docker compose up` on a clean checkout → migrations auto-run → both replicas healthy
→ `make demo` shows cross-instance fan-out + persistence → bad/missing WS auth closes
`4401` → graceful shutdown drains. `make check` (ruff + ty + pytest, 15 tests) is green.
