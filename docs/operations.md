# Operations

Day-to-day running of a deployed Tandem stack. Backups have their own page:
[docs/backup-restore.md](backup-restore.md).

## Logs

```bash
docker compose logs -f server
```

Swap `server` for `db` or `web`. Things worth grepping for:

| What | Where |
|---|---|
| Generated superadmin password (first boot only) | `docker compose logs server \| grep -i superadmin` |
| Migration failures on start | `docker compose logs server \| grep entrypoint` |
| Transcription queue activity | `docker compose logs server \| grep queue-manager` |

The server also writes the same stream to `$APP_DATA_DIR/logs/server.log` (10 MB rotating, 5
backups), which survives a container recreate — useful when `docker compose logs` has already
rolled past what you need. The log level is INFO; the detailed per-file `[metadata]` scan
decisions are DEBUG and won't appear.

## Upgrading

```bash
git pull
docker compose up -d --build
```

The server's entrypoint runs `alembic upgrade head` before uvicorn starts, so schema changes apply
themselves. If the server exits immediately after an upgrade, read the entrypoint lines first — a
failed migration stops the boot deliberately rather than running against a half-migrated schema.

**A database that predates Alembic must be stamped once**, before the first deploy of an
Alembic-era image, or `upgrade head` will try to create tables that already exist:

```bash
docker compose run --rm server alembic stamp head
```

`docker-compose.yml` is gitignored, so `git pull` never touches your secrets or volume paths.
When the template changes, diff `docker-compose.example.yml` against your copy and port over what
you want.

**DRM plugins are opt-in.** A deployment that uses the ACSM or Audible import sources must build
the server image with `docker compose build --build-arg INSTALL_DRM_PLUGINS=1` — the default image
ships without the DeACSM/DeDRM Calibre plugins, and `.acsm` conversion is unavailable without
them. See [import-sources.md](import-sources.md).

## Client support window

The Android app is distributed through Play, so it updates on each user's schedule while this
server updates on yours. The two drift, and the handshake that keeps that drift legible is a
single integer:

- `server/version.py` — `API_VERSION`, what this server speaks. `GET /api/health` reports it
  (unauthenticated, so a client can ask before it has credentials) alongside `app_version`, the
  human-facing release string.
- `android/.../data/remote/VersionCompat.kt` — `SUPPORTED_API_VERSION`, what an installed app was
  built against.

```bash
curl -s https://tandem.example.com/api/health
# {"status":"healthy","app_version":"0.1.0","api_version":1}
```

**The support window is one API version: a server supports every client on the same
`API_VERSION`.** The app does not refuse to run outside it — a mismatch is a banner, not a wall,
and most of the API keeps working — but nothing is guaranteed, and the banner names whichever side
is behind ("update the app from Play", or "ask your server admin to upgrade").

Practical consequences when you upgrade a server across an `API_VERSION` bump:

- Users still on the older app see "update it from Play" — the server is ahead of them — until
  Play delivers the new build. That is expected, and it is what the alternative, a bare 404, could
  not explain.
- Rolling the server back past a bump flips it: apps that already updated then say "ask your
  server admin to upgrade". Both directions are visible, neither is fatal.
- A server that predates the handshake entirely (no `api_version` in `/api/health`) shows no
  banner at all: unknown is not a mismatch.

`API_VERSION` is bumped only for a change a current client cannot survive — a removed or renamed
response field, a changed type, a removed endpoint, a newly required request field. Additions are
not breaks. The rule and the release checklist live in `server/version.py` and
[android.md](android.md).

## Reverse proxy

If you put Tandem behind a reverse proxy (Caddy, Traefik, the shipped `web` nginx container is
one already), every request reaches the API from the proxy's address. Unless uvicorn is told to
trust that peer, all clients look like one: the `5/minute` rate limit on login/registration
becomes a single global bucket (five bad logins a minute from anyone keeps *everyone* at 429),
and audit-log rows record the proxy's IP instead of the client's.

Set `FORWARDED_ALLOW_IPS` on the `server` service to the address your proxy connects from —
check the server log's `request.client.host` if unsure; a CIDR or comma-separated list is
accepted (the template ships `172.16.0.0/12`, covering Docker's default bridge networks):

```yaml
  server:
    environment:
      - FORWARDED_ALLOW_IPS=172.16.0.0/12
```

uvicorn then rewrites the client address from `X-Forwarded-For`, but **only** for requests
arriving from those peers. Never set it to `*` — that trusts an attacker-chosen header from
anywhere, letting one client dodge rate limits and forge audit IPs. With `APP_ENV=prod`, the
server logs a startup warning when the variable is unset.

Two hardening notes for an internet-facing deployment:

- Prefer running your outer proxy (e.g. Caddy) **on the compose network**, pointed directly at
  `server:8000` — one proxy hop, one address to trust, and the API port never needs publishing.
- Bind any published ports to loopback or your LAN (`127.0.0.1:8000:8000`-style) or firewall
  them, so clients can't bypass the proxy and hand uvicorn a spoofed header from a "trusted"
  network path — and so the un-proxied ports aren't reachable from the internet at all.

The `web` nginx proxy forwards `X-Forwarded-For` (appending to any incoming chain via
`$proxy_add_x_forwarded_for`) and `X-Forwarded-Proto`, so a client's real address survives both
the Caddy → web → server and the direct web → server topologies.

## Login throttling

`POST /api/auth/login` is guarded by two independent limits.

| Layer | Keyed on | Default | Tune with |
|---|---|---|---|
| slowapi bucket | client IP | 5 requests/minute | code (`server/rate_limit.py`) |
| Failed-attempt tracker | username | 10 **failed** attempts per 15 minutes | `LOGIN_FAILURE_LIMIT`, `LOGIN_FAILURE_WINDOW_SECONDS` |

The per-IP layer is only as good as the client address — see "Reverse proxy" above; behind
docker NAT it can collapse into one bucket for everybody, which is what the username layer is
there to cover. Both stay on: the IP bucket also meters *spraying* (many accounts, one guess
each), which the username bucket by design does not.

The username layer counts **failures only**, in a sliding window, keyed on the username
case-folded and stripped (`Alice`, `alice` and `" alice "` are one bucket). A correct password
clears the counter. Once the bucket is at the limit, further attempts for that username get
**429** with a `Retry-After` header (seconds until it drops back below the limit) and a
`login_locked` audit row — checked *before* the password is verified, so a locked bucket answers
429 whether or not the password was right. That is deliberate: otherwise the response would be a
password oracle.

**The trade-off to understand before retuning:** because the bucket is keyed on the username,
anyone who knows a username can hold it at the limit and keep the real user out for the window.
That is inherent to username keying, and the defaults are chosen around it — a short window (a
delay, never a takeover) and a high threshold (10 is far more consecutive failures than a real
user's typos, and any success in between resets it). Lowering `LOGIN_FAILURE_LIMIT` makes that
lockout cheaper to inflict; raising it weakens brute-force protection. There is no admin "unlock"
action: wait out the window, or restart the server (the counters are in memory).

Counters live in the server process, so they reset on restart and are **per worker**. Tandem runs
a single uvicorn worker; if you ever scale that up, each worker keeps its own counts and the
effective threshold multiplies — replace `FailedLoginTracker` in `server/rate_limit.py` with a
shared backend before doing so.

Both `login_failed` and `login_locked` rows land in the audit log, so a sustained attack on one
account is visible in the web UI's user-management **Audit Log** tab, filterable by action (and it
grows that table; the per-IP limit is what bounds how fast).

## Single process only

**Run exactly one `server` process.** Not `uvicorn --workers 2`, not `WEB_CONCURRENCY=2`, not a
second replica of the container behind a load balancer. `server/entrypoint.sh` starts one uvicorn
process with no `--workers`, and that is a correctness requirement, not a default.

Four things in the server are process-local (issue #252):

| What | Where | What a second process does |
|---|---|---|
| Queue claim | `services/queue_manager.py`, `_process_next_item` | Claims with `SELECT ... WHERE status='pending' LIMIT 1` then `UPDATE` — no row lock, so both processes claim the same item and the same audiobook is transcribed twice |
| Cancel / pause state | `services/queue_manager.py`, module-level `_cancel_requested` / `_pause_requested` / `_active_provider` | A cancel or an off-hours pause only reaches whichever process owns the job; the other keeps going |
| Startup recovery | `services/queue_manager.py`, `reset_stale_items()` | Flips *every* `in_progress` row back to `pending` at boot, re-queueing the other process's running job |
| Schedulers | `main.py` lifespan → `import_scheduler`, `backup_service` | N processes means N nightly `pg_dump`s and N concurrent import syncs |

`config.check_single_process()` refuses to boot when `WEB_CONCURRENCY`, `UVICORN_WORKERS` or
`GUNICORN_WORKERS` is greater than 1, so the mistake surfaces in the container log rather than as a
duplicated transcription weeks later. A healthy start logs:

```
Single-process mode: one queue manager, one import scheduler, one backup scheduler (issue #252).
```

and a refused one raises `RuntimeError: WEB_CONCURRENCY=2 would run the server as 2 processes ...`.

To scale, give the one process more CPU. Making the server genuinely multi-process is a redesign —
an atomic claim (`UPDATE ... WHERE id=:id AND status='pending' RETURNING id`, or Postgres'
`SELECT ... FOR UPDATE SKIP LOCKED`), cancellation moved onto the queue row, and a
`worker_id`/heartbeat so startup recovery only touches rows owned by a dead process. Do that work
before removing the guard, not after.

## Restart policies

Every service in `docker-compose.example.yml` carries `restart: unless-stopped`. Keep it that way
in your copy. Without it on `server` and `db`, a host reboot leaves those two containers exited
while `web` restarts on its own and crash-loops against a missing `server` upstream
(`host not found in upstream "server"`) — which is exactly what happened after a host OS upgrade
(issue #105). A quick audit:

```bash
docker compose ps
```

## Rotating the Postgres password

`POSTGRES_PASSWORD` is only applied by Postgres on **first initialization** of the data volume.
On an existing deployment, editing `docker-compose.yml` alone won't rotate the stored password —
the `server` container will just fail to authenticate. Rotate it in place first:

```bash
docker compose exec db psql -U booksync -d booksync -c "ALTER ROLE booksync WITH PASSWORD 'your-new-generated-password';"
```

Then update `POSTGRES_PASSWORD` and `DATABASE_URL` in `docker-compose.yml` to match, and restart
the `server` service. This does not touch or wipe any data in the `booksync_db` volume.

## Rotating credential encryption keys

`CREDENTIAL_ENC_KEYS` is a comma-separated list of Fernet keys. The first key encrypts new writes;
every key in the list is tried for decryption. So rotation is "prepend a new key and keep the old
one around" — no migration step, and no re-entering of import-source credentials.

Generate a new key with:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Keep every key you've ever used until you're certain no stored credential still needs it. Losing
them means the encrypted Audible/ABS credentials in your backups can't be decrypted.

## Upload size limits

The server refuses any `multipart/form-data` request whose declared `Content-Length` exceeds
`UPLOAD_MAX_BYTES` (default 10 GiB) with **413**, *before* the multipart parser touches the body.
This matters because FastAPI parses an upload body before it resolves the route's auth
dependency — without the cap, an unauthenticated caller could feed the parser arbitrarily large
bodies (issue #157). Uploads are multi-GB audiobooks, so keep the value generous.

That pre-parse cap is one of three layers (issue #151):

1. **Proxy edge cap** — the fronting reverse proxy bounds the raw request body, including
   chunked (no-length) bodies, before anything reaches uvicorn. A ready-made Caddy template
   with per-route caps (4GB for the big-upload routes, 16MB for the rest of `/api/*`) ships
   as `Caddyfile.example` at the repo root — copy and adapt it rather than writing your own.
2. **Pre-parse whole-body cap** — `UPLOAD_MAX_BYTES` above, enforced by `server/middleware.py`
   on the declared `Content-Length` before the multipart parser runs.
3. **Per-file streamed caps** — `MAX_UPLOAD_FILE_BYTES` (default 4 GiB) and `MAX_COVER_BYTES`
   (default 16 MiB) bound each uploaded file while it streams to disk
   (`server/services/uploads.py`). These count actual bytes, so they hold even when the
   `Content-Length` header is missing or lying.

Keep the proxy limit at or above `UPLOAD_MAX_BYTES`, and the per-file cap at or below it —
otherwise one layer rejects uploads another would have accepted.

## Storage

| Mount | Grows with | Notes |
|---|---|---|
| ebooks / audiobooks | your library | Read-only as far as the server is concerned, apart from format conversion writing an `.epub` sibling |
| `/data/app` | covers, working files | Extracted cover art accumulates; Troubleshoot can delete orphaned covers |
| `/data/imports` | ACSM import staging | Inbox / processed / failed subfolders |
| `/backups` | nightly dumps | Retention is configurable in System → Backups. **Put this on a different disk than the DB** |

### Exactly one mount per container path

`docker-compose.example.yml` binds `./data:/data/app`, and that must stay the *only* mount
targeting `/data/app`. Compose accepts a second source on the same container path — a named volume
plus the bind, say — but only one can be in effect. The other becomes an invisible copy: covers and
`logs/server.log` written to whichever is live, backups restored into whichever is not, and
removing the "wrong" line later silently swaps the whole directory.

To check an existing deployment:

```bash
docker inspect -f '{{json .Mounts}}' book-sync-server-1 | python3 -m json.tool
```

If two entries share `"Destination": "/data/app"`, copy anything unique out of the inactive source,
delete that line from `docker-compose.yml`, and `docker compose up -d`.

`./data` is a bind mount **inside the git checkout** (`data/` is gitignored, so nothing leaks, but
it is still there). Never run `git clean -xfd` in the checkout during an upgrade — it deletes the
covers, working files and logs along with the untracked build junk you meant to remove.

## Users

The first account is the superadmin created on first boot. Additional users register themselves
(`ALLOW_PUBLIC_REGISTRATION`, on by default) but land inactive until an admin approves them — set
`ALLOW_PUBLIC_REGISTRATION=false` to close registration entirely.

Reading position, bookmarks and progress are **per user**. Two people using the same server keep
separate positions in the same book.

### Audit log retention

Security-relevant events (logins, failed logins, lockouts, role and password changes) go to the
`audit_logs` table, readable by admins in the user-management Audit Log tab. Each row holds a user id, the
client IP and a short description — on a failed login that description includes the username that
was typed — so it is the most personal table in every nightly dump.

Rows are deleted once they are older than **`audit_log_retention_days`, which defaults to 90**.
Set it to `0` to keep the log forever. The value lives in system settings (`PUT /api/settings/`,
admin only) and the prune runs on the backup scheduler's tick (every 10 minutes), so a change
takes effect without a restart. The `details` text is capped at 500 characters at write time.
