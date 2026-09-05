# API reference

The server is the integration surface: the web app, the Android app and any script you write all
speak to the same HTTP API. This page is the hand-written orientation — the base URL, how you
authenticate, what the roles mean, and where each family of routes lives. The complete,
machine-readable inventory of every route, request body and response model is
**[openapi.json](openapi.json)**, generated from the running app and kept in sync by CI.

## Base URL and prefix

Every route is under `/api` except the root banner. These three need no token:

| Path | What it is |
|---|---|
| `GET /` | Name, version, status. Unauthenticated. |
| `GET /api/health` | Readiness: verifies the database answers. `503 {"status": "unhealthy", "db": "down"}` when it does not. The healthy body carries `app_version` and `api_version` — the version handshake a client can make **before** it has credentials. |
| `GET /api/livez` | Liveness: static `200` while the process is up. |

A typical deployment puts a reverse proxy in front and forwards `/api/*` to the server and
everything else to the web container, so the API's public base is the same origin as the web app.
Clients therefore use relative `/api/...` paths; the Android app takes the server's base URL and
appends `/api`.

## Authentication

JWT bearer tokens, HS256, signed with `JWT_SECRET_KEY`.

```
POST /api/auth/login       {"username": "...", "password": "...", "device_id": "..."}
  -> 200 {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}
```

Send the access token on every subsequent request:

```
Authorization: Bearer <access_token>
```

- **Access token: 24 hours.** Carries `sub` (user id), `ver` (the account's `token_version`) and
  `sid` (the session it was minted from).
- **Refresh token: 30 days.** Exchange it for a fresh pair:

  ```
  POST /api/auth/refresh   {"refresh_token": "...", "device_id": "..."}
  ```

  Refresh re-issues **for the same session** rather than replacing it. The presented refresh token
  stays valid until its own expiry — this is deliberately *not* rotation, so a refresh whose
  response is lost cannot strand a device with a dead token and no way to renew. Clients
  single-flight their refreshes (`web/src/api.js`, Android's `TokenAuthenticator`) so concurrent
  401s produce one refresh, not a stampede.

- `POST /api/auth/register` — self-registration. New accounts land inactive; an admin approves
  them (`POST /api/users/{id}/approve`). Logging in before approval returns
  `403 "Your account is pending admin approval"`.
- `GET /api/auth/me`, `PUT /api/auth/me` — the current user's profile.
- `POST /api/auth/change-password`.

### Sessions and sign-out (issue #250)

Every login opens a **session**: a `refresh_tokens` row holding the refresh token's `jti` plus the
client's `device_id`. Access and media tokens minted from it carry the same value as `sid`, and both
the auth dependency and the media resolver refuse a token whose session has been revoked.

| Route | Effect |
|---|---|
| `POST /api/auth/logout` | Revokes **one** session — the calling device. Every other device stays signed in. Responds `{"message": ..., "scope": "device"}`. |
| `POST /api/auth/logout-all` | Bumps `users.token_version`, invalidating every token the account holds, everywhere. Responds with `"scope": "all"`. |

`token_version` is the account-wide kill switch; it is also bumped by a password change and by an
admin password reset. A token issued before sessions existed (no `jti`/`sid`) is still accepted on
its `ver` alone, so a client that has not been updated keeps working across a deploy.

Signing out one device rather than all of them matters because the phone is usually the device
holding unsynced reading positions — see [position-sync-contract.md](position-sync-contract.md).

### Media tokens

Cover images and audio are fetched by URLs that cannot carry an `Authorization` header — an `<img>`
tag, a Cast receiver, an `<audio>` element. For those, mint a short-lived token scoped to one
resource:

```
GET  /api/auth/media-token?resource_type=cover&resource_id=<id>
POST /api/auth/media-token/batch   {"resources": [{"resource_type": "...", "resource_id": "..."}]}
```

`resource_type` is `cover` or `audiobook`; anything else is `400`. The batch form takes up to 200
resources in one round-trip (the library grid mints every visible cover at once) and returns a map
keyed `"<type>:<id>"`. Both responses carry `expires_in` in seconds. The token belongs to the same
session as the access token that requested it, so revoking the session kills the media URLs too.

### Forced password reset

The bootstrap superadmin (and any admin-reset account) is flagged `must_reset_password`. Until the
password is changed the server refuses **every** route except `GET /api/auth/me`,
`POST /api/auth/change-password` and `POST /api/auth/logout`, with `403 password_reset_required`.
This is enforced server-side, so it holds for `curl` exactly as it does for the web app.

### Rate limits and lockout

`/api/auth/login` and `/api/auth/register` are rate-limited (5/minute). On top of that, repeated
failures for one username lock that username briefly: `429` with a `Retry-After` header, refused
before the password is even checked so it cannot be used as a password oracle.

## Roles

Four roles, strictly ordered:

| Role | Can |
|---|---|
| `user` | Read the library, read and write **their own** reading positions, bookmarks and progress. |
| `editor` | Everything above, plus write to the library: upload, edit metadata, pair/unpair, delete, scan, run the troubleshooter, and the maintenance endpoints (`GET /api/library/verify`, `GET /api/library/debug-metadata/...`). |
| `admin` | Everything above, plus system settings, users, import sources, backups, and the rest of transcription control. |
| `superadmin` | Everything above, plus the destructive backup routes (restore, delete) and any change to another superadmin's account. Bootstrapped on first run. |

The floors are per route, not per prefix, and the OpenAPI document does not encode them (it only
records that a route needs a bearer token). The authoritative answer is the `Depends(...)` on the
route — `get_current_user`, `get_editor_user`, `get_admin_user`, `get_superadmin_user` in
`server/routers/auth.py`. Some routes add a further inline check on top: an admin cannot, for
example, change a superadmin's role.

A route below your role answers `403 {"detail": "Requires <role> role or higher"}`.

Two role-scoped responses are worth knowing about because they are not simply allow/deny:

- **`GET /api/settings/`** returns the full configuration to an admin, and to everyone else only
  `{"abs_enabled": bool, "hardcover_configured": bool}` — the two facts the reader UI actually
  needs. Secrets are masked as `"********"` in the admin payload and never appear in the trimmed
  one; `hardcover_configured` is a derived boolean, not the token (issue #263).
- **Positions** are always scoped to the calling user. There is no route that reads another user's
  position.

## Route families

| Prefix | Covers | Typical floor |
|---|---|---|
| `/api/auth` | Login, refresh, logout, profile, media tokens | open / `user` |
| `/api/users` | Account listing, approval, roles, password reset, audit log | `admin` |
| `/api/library` | Ebooks, audiobooks, pairs, uploads, scanning, metadata, chapters, metadata matching | `user` to read, `editor` to write |
| `/api/sync` | Reading position, progress, bookmarks, text matching | `user` (own data) |
| `/api/files` | Cover images, ebook and audiobook bytes, sync maps | media token or `user` |
| `/api/transcription` | Queue, status, transcript text, realignment | `user` to read, `editor` to queue/cancel, `admin` for the rest |
| `/api/settings` | System settings, connection tests | `admin` to write; trimmed read for everyone else |
| `/api/import` | Audible and ACSM import sources | `admin` |
| `/api/troubleshoot` | Library scans, repairs, bulk fixes | `editor` (a couple of reads at `user`) |
| `/api/stats` | Disk usage, backups, restore | `admin`, and `superadmin` for the destructive backup routes |

Position and bookmark writes all go through `PUT /api/sync/position/{scope}/{ident}` — there is no
other write path, and the rules are non-obvious. **Read
[position-sync-contract.md](position-sync-contract.md) before touching them.**

## Error shapes

FastAPI's conventions, unchanged:

```json
{"detail": "Requires editor role or higher"}
```

Request-validation failures (`422`) return the structured form instead:

```json
{"detail": [{"loc": ["body", "username"], "msg": "field required", "type": "missing"}]}
```

| Status | Means |
|---|---|
| `400` | Malformed input the route validated itself (a bad `resource_type`, an unsafe URL). |
| `401` | Missing, expired, malformed or revoked token. Refresh, then retry. |
| `403` | Authenticated but not allowed — wrong role, inactive account, or `password_reset_required`. |
| `404` | No such row, or a scope identifier that resolves to nothing. |
| `409` | Conflict. Two flavours: a duplicate (username taken, pair already exists, already queued), and a **stale position write** — `PUT /api/sync/position/...` returns `409` with the authoritative `PositionResponse` body and changes nothing, so the client can reconcile. |
| `413` | Upload larger than `UPLOAD_MAX_BYTES`. Refused before the multipart parser runs, so it is reachable without a token. |
| `422` | Body or query failed schema validation. |
| `429` | Rate limit or login lockout. Honour `Retry-After`. |
| `503` | `GET /api/health` only: the database is unreachable. |

## Getting the full schema

**The committed export.** [`docs/openapi.json`](openapi.json) is the complete OpenAPI 3.1 document,
regenerated from the app and drift-gated in CI (`server/tests/test_openapi_export.py` locally). Feed
it to a client generator, or read it directly. Regenerate it after changing any route:

```bash
python server/scripts/export_openapi.py           # rewrite docs/openapi.json
python server/scripts/export_openapi.py --check   # fail if it has drifted
```

**The interactive UI, in dev only.** With `APP_ENV=dev` the server serves Swagger UI at `/docs`,
ReDoc at `/redoc` and the live document at `/openapi.json`. With `APP_ENV=prod` — the default —
all three return `404`: publishing the full route inventory to anyone who can reach the port buys
nothing, and the container's published port is normally reachable on the LAN even when the reverse
proxy forwards only `/api/*` (issues #260, #263).

```bash
APP_ENV=dev uvicorn main:app --reload   # from server/, then open http://localhost:8000/docs
```

Note that a fair number of routes still return hand-built dicts with no declared `response_model`,
so their responses show as untyped in the schema. That set only ever shrinks:
`server/tests/test_openapi_contract.py` holds the allow-list and fails on any new untyped route.
