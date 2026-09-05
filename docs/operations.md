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

### Rolling back

**Take a manual backup (System → Backups) before every upgrade** — it is what makes the last
resort possible. To undo one, check out the previous tag and rebuild; if the upgrade ran a
migration, downgrade the schema or restore the pre-upgrade dump. The full procedure, including how
to find the Alembic revision the old code expects, is in [releasing.md](releasing.md), "Rolling
back".

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

## Monitoring

Every health signal Tandem exposes is pull-only. Nothing here pages you on its own — the API can
be down, or the nightly backup can have been failing for a week, and the only place that shows is
a badge somebody has to open the System page to see. Point an external checker at the three
endpoints below and that stops being true.

Any checker works — Uptime Kuma on the same host, or a hosted monitor. Run it **outside** the
compose stack: one that dies with the server cannot tell you the server died. The compose
healthcheck on `server` (`docker-compose.example.yml`) is not a substitute; it feeds Docker's
container status and notifies nobody.

| Check | Expect | Interval | Alert when |
|---|---|---|---|
| `GET /api/livez` | 200 `{"status":"alive"}` | 60 s | non-200, or no answer in 10 s, twice running |
| `GET /api/health` | 200 `{"status":"healthy",...}` | 60 s | 503, or no answer in 15 s, twice running |
| `GET /api/health/backup` | 200 `{"status":"ok","age_hours":N}` | 1 h | body contains `"stale"` or `"never"`, twice running |

All three are **unauthenticated** — that is what makes them wireable. Access tokens last 24 h, so
anything requiring a login cannot be polled by a monitor without a token-refresh dance.

"Twice running" everywhere: a single missed poll during a restart or a `docker compose up -d
--build` is normal and should not page anyone.

### What each one tells you

**`/api/livez`** is liveness — the process is up, dependencies unchecked. It answers during a
database outage, so `livez` up + `health` down localizes the fault to Postgres without a single
log line.

**`/api/health`** is readiness: it runs `SELECT 1` and returns 503 when the database does not
answer inside 5 s (issue #47). This is the one that matters most. First moves when it fires:

```bash
docker compose ps                    # is db up at all?
docker compose logs --tail=100 server
docker compose logs --tail=100 db
```

The healthy payload also carries `app_version` and `api_version` (see "Client support window"),
so the same check doubles as a record of when a deploy landed.

**`/api/health/backup`** is backup freshness, and nothing else (issue #233):

```bash
curl -s https://tandem.example.com/api/health/backup
# {"status":"ok","age_hours":9}
```

| `status` | Means |
|---|---|
| `ok` | a dump exists and is newer than 36 hours |
| `stale` | a dump exists but is older than 36 hours — the nightly run is failing |
| `never` | there is no dump at all, or the backups directory is unreadable |

`never` covers the unreadable-directory case deliberately: a missing NAS mount and a missing
backup deserve the same page, and a probe that 500'd instead would look identical to the API
being broken.

It stays **200** in all three states. A stale backup is a real problem but not a readiness
failure, and a 503 here would pull the service out of a load balancer over a backup that did not
run. So the alert rule has to match on the body, not the status code — for example, in Uptime
Kuma: monitor type HTTP(s) - Keyword, keyword `"status":"ok"`, **invert keyword** off, retries 2.
Any checker that supports a body assertion can express the same thing:

```bash
# Equivalent as a shell check (exit 1 = alert)
curl -sf https://tandem.example.com/api/health/backup | grep -q '"status":"ok"'
```

When it fires, work through [backup-restore.md](backup-restore.md) — the usual causes are a full
or unmounted `BACKUPS_DIR` and a `pg_dump` failure, both of which are in `docker compose logs
server | grep backup-service`. The admin console's System page has the detail this probe
deliberately withholds (location, filename, size, exact timestamp) at `GET /api/stats/backup`.

The probe costs one directory listing and one stat, runs off the event loop, and is cached for
`BACKUP_PROBE_CACHE_SECONDS` (default 60) — so polling it more often than that is free, and a
hostile loop against an unauthenticated endpoint cannot hammer the NAS mount. It never touches
the database, which is what keeps it answering during exactly the outage that makes
`/api/health` fail.

### Not covered yet

A transcription job wedged in `processing` is not detectable from outside — `queue_manager.py`
sets `started_at` but never compares it against a deadline. That watchdog is tracked separately;
when it lands, a fourth check belongs in the table above.

## Rate limits on expensive reads

A handful of read endpoints stat every file in the library, walk a data root, or shell out. The
server runs a **single uvicorn worker** (see "Single process only"), so while one of those runs
synchronously nothing else is served — including login and `/api/health`, whose compose
healthcheck gives up after 10 s. One authenticated user in a loop was enough to stall the whole
deployment (issue #208).

Three fixes are layered, and all three are load-bearing:

1. **Off the event loop.** The blocking work runs in a worker thread (`asyncio.to_thread`), so a
   slow NAS mount delays that one request instead of every request.
2. **Cached.** Results are reused for a short TTL, so a page that polls and a hostile loop cost
   the same.
3. **Rate limited per user**, as a backstop for whatever the first two miss.

| Bucket | Endpoints | Default | Tune with |
|---|---|---|---|
| Expensive reads | `/api/troubleshoot/issues`, `/api/library/verify`, `/api/stats/disk_usage`, `/api/library/calibre-status` | 30 requests / 60 s | `EXPENSIVE_READ_LIMIT`, `EXPENSIVE_READ_WINDOW_SECONDS` |
| Search reads | `/api/library/search`, `/api/transcription/queue/history` | 60 requests / 60 s | `SEARCH_READ_LIMIT`, `SEARCH_READ_WINDOW_SECONDS` |
| External metadata | `POST /api/library/match/search` | 20 requests / 60 s | `EXTERNAL_METADATA_SEARCH_LIMIT`, `EXTERNAL_METADATA_SEARCH_WINDOW_SECONDS` |

| Cache | Default TTL | Tune with |
|---|---|---|
| `/api/stats/disk_usage` | 300 s | `DISK_USAGE_CACHE_SECONDS` |
| `/api/library/calibre-status` | 300 s | `CALIBRE_STATUS_CACHE_SECONDS` |
| Per-audiobook chapter-encoding check (`/api/troubleshoot/issues`) | 300 s | `CHAPTER_ENCODING_CACHE_SECONDS` |
| `/api/health/backup` | 60 s | `BACKUP_PROBE_CACHE_SECONDS` |

Buckets are keyed on the **authenticated user's id**, not the client address — behind a proxy and
docker NAT every caller shares one address (see "Reverse proxy"), so an IP bucket would throttle
the whole deployment together and protect nobody. Over the limit is **429** with a `Retry-After`
header naming the seconds until the window drops back below it. Same sliding-window machinery as
the login throttle, so the same caveat applies: counters live in the server process, reset on
restart, and are per worker.

Unlike the auth buckets these count **every** request, not just failures, which is why the
numbers are an order of magnitude higher. The UI fires each of these reads once per page load;
only a loop reaches the ceiling. If you legitimately hit one — a script doing a bulk metadata
pass, say — raise the matching limit rather than removing the dependency, and remember that the
external-metadata bucket is metering an API quota you pay for, not server CPU.

The `chapter_encoding` cache is keyed on `(path, mtime, size)`, so repairing a file makes the next
Troubleshoot load re-check it immediately regardless of the TTL.

**Who may ask at all** is the fourth layer, and the cheapest one: all four expensive reads are
curation or operator views, so none of them is open to a plain `user` (issue #208).

| Endpoint | Minimum role |
|---|---|
| `/api/stats/disk_usage` | admin |
| `/api/troubleshoot/issues` | editor |
| `/api/library/verify` | editor |
| `/api/library/calibre-status` | editor |

The role check runs **before** the bucket (`routers.auth.rate_limited` wraps the role dependency
rather than sitting beside it), so a caller who may not use the endpoint gets 401/403 and spends
nothing -- otherwise any account could drain an editor-only bucket and deny it to the editors.

The web hides the matching navigation and route behind `RequireRole` so a below-role account is
never shown a console it cannot use, but that is presentation only. The dependency above is the
boundary; the role travels in a JWT the client cannot be trusted to police.

### Page-size caps

Every list endpoint has a ceiling, and a `limit` over it is refused with **422** rather than
silently served.

| Endpoint | Cap | Over it |
|---|---|---|
| `/api/library/{ebooks,audiobooks,pairs,items,...}` | `limit=500` | 422 |
| `/api/transcription/queue/history` | `limit=200` | 422 |
| `/api/sync/bookmark/{pair}/log` | `limit=200` | 422 |
| `/api/library/search` | 200 rows per section | `truncated: true` |
| `/api/library/verify` | 200 orphans per list | `truncated: true` |

The last two are not paginated, so they cap the body instead of rejecting the request, and set
`truncated` so a client can tell "nothing else matched" from "there is more". For `verify` that
matters: a library whose mount has dropped is *entirely* orphaned, and the useful answer is the
first 200 plus a flag, not one row per book. Clean those up and run it again for the next batch.

`%` and `_` in a search term are matched **literally**, not as SQL wildcards — `q=%` used to
return the whole library three times in one response.

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

## Sessions and signing out

Each sign-in opens a **session** — one row in `refresh_tokens`, holding the
refresh token's `jti`, the client's `device_id`, and when it was issued, last
used and revoked. The refresh token carries that `jti`; the access and media
tokens minted from it carry the same value as `sid`. Sessions are what make
sign-out per-device (issue #250).

| Action | Scope | Mechanism |
|---|---|---|
| `POST /api/auth/logout` | this device | revokes one `refresh_tokens` row |
| `POST /api/auth/logout-all` | every device | `token_version + 1`, and revokes every row |
| `POST /api/auth/change-password` | every device | same |
| Admin password reset (`POST /api/users/{id}/reset-password`) | every device | same |

`logout` ends the session named by the refresh token in the request body, or —
if the client sends no body — the one named by the access token that
authenticated the call. Only when neither names a session (a token issued before
sessions existed) does it fall back to the account-wide bump. It is idempotent:
signing out of a session that is already revoked answers 200 and does *not*
escalate to the global revoke, so a retried logout cannot sign the account out
everywhere.

`users.token_version` is unchanged and is still the account-wide kill switch. It
is checked on every route, including the media/download endpoints in
`routers/files.py`, which check the session too.

Operationally:

- **Nothing to run on deploy.** Migration `0013_refresh_tokens` only creates the
  table. Tokens already in the wild carry no `jti`/`sid`, are still accepted on
  their `ver` exactly as before, and are upgraded onto a session on the client's
  first refresh — so nobody is signed out by the upgrade. Until that first
  refresh, an old client's logout still signs out every device (it has no
  session to name), which is what it was built to do.
- **The table prunes itself.** Every login deletes that user's sessions unused
  for longer than `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (they can no longer
  authenticate — the JWT's own `exp` refuses them) and revokes any earlier live
  session with the same `device_id`, so one device holds one session.
- **Lost device?** `POST /api/auth/logout-all` (or a password change) is the
  answer; a per-device logout from another device cannot revoke the lost one,
  because the caller does not hold its refresh token. The user can do this
  themselves — **Sign out everywhere**, in the web app's sidebar (and its mobile
  drawer) and on the Android **Account** screen under Log out. Both confirm
  first, and both clear the local tokens whether or not the call got through.
  Calling it by hand with the account's own access token still works.
- **Auditing**: a device logout writes a `logout` row whose details say
  `(device)` or `(all)`; `logout-all` writes `logout_all`.

## Single process only

**Run exactly one `server` process.** Not `uvicorn --workers 2`, not `WEB_CONCURRENCY=2`, not a
second replica of the container behind a load balancer. `server/entrypoint.sh` starts one uvicorn
process with no `--workers`, and that is a correctness requirement, not a default.

Five things in the server are process-local (issues #252, #202):

| What | Where | What a second process does |
|---|---|---|
| Queue claim | `services/queue_manager.py`, `_process_next_item` | Claims with `SELECT ... WHERE status='pending' LIMIT 1` then `UPDATE` — no row lock, so both processes claim the same item and the same audiobook is transcribed twice |
| Cancel / pause state | `services/queue_manager.py`, module-level `_cancel_requested` / `_pause_requested` / `_active_provider` | A cancel or an off-hours pause only reaches whichever process owns the job; the other keeps going |
| Startup recovery | `services/queue_manager.py`, `reset_stale_items()` | Flips *every* `in_progress` row back to `pending` at boot, re-queueing the other process's running job |
| Schedulers | `main.py` lifespan → `import_scheduler`, `backup_service` | N processes means N nightly `pg_dump`s and N concurrent import syncs |
| Library job guard | `services/library_jobs.py`, module-level `_state["running"]` | The 409 that stops `/library/scan`, `/rescan-all`, `/rehash` and `/enrich-abs` overlapping is a flag in one process's memory, so two processes run two scans; they then race on `ebooks.file_path` / `audiobooks.file_path` and the loser of every insert falls back to re-reading the winner's row (`routers/library.py`, `_insert_or_reread`) |

`config.check_single_process()` refuses to boot when `WEB_CONCURRENCY`, `UVICORN_WORKERS` or
`GUNICORN_WORKERS` is greater than 1, so the mistake surfaces in the container log rather than as a
duplicated transcription weeks later. A healthy start logs:

```
Single-process mode: one queue manager, one import scheduler, one backup scheduler (issue #252).
```

and a refused one raises `RuntimeError: WEB_CONCURRENCY=2 would run the server as 2 processes ...`.

To scale, give the one process more CPU. Making the server genuinely multi-process is a redesign —
an atomic claim (`UPDATE ... WHERE id=:id AND status='pending' RETURNING id`, or Postgres'
`SELECT ... FOR UPDATE SKIP LOCKED`), cancellation moved onto the queue row, a `worker_id`/heartbeat
so startup recovery only touches rows owned by a dead process, and the library job guard moved to a
row or a Postgres advisory lock. Do that work before removing the guard, not after.

## Restart policies

Every service in `docker-compose.example.yml` carries `restart: unless-stopped`. Keep it that way
in your copy. Without it on `server` and `db`, a host reboot leaves those two containers exited
while `web` restarts on its own and crash-loops against a missing `server` upstream
(`host not found in upstream "server"`) — which is exactly what happened after a host OS upgrade
(issue #105). A quick audit:

```bash
docker compose ps
```

## Running as a non-root user

The `server` and `web` containers run as an unprivileged uid, not root (issue #180). `server` is
the internet-facing process, it shells out to Calibre, ffmpeg, `pg_restore` and `rsync`, and every
one of its mounts — both library roots, the import staging area, the backup share — is read-write.
As uid 0 with a full capability set, one path bug there reached all of it at once.

Three keys in `docker-compose.example.yml` carry this:

```yaml
user: "${PUID:-1000}:${PGID:-1000}"
cap_drop:
  - ALL
security_opt:
  - no-new-privileges:true
```

`db` gets only `no-new-privileges`. The official postgres image's entrypoint starts as root, fixes
ownership on the data directory and drops to the `postgres` user itself, so a `user:` skips that
setup and `cap_drop: [ALL]` takes away the `CHOWN`/`SETUID`/`SETGID`/`DAC_OVERRIDE`/`FOWNER` it
needs to perform it — either one turns a working database into a boot loop.
`server/tests/test_compose_contract.py` fails if someone "finishes" the hardening there.

The **Jetson worker is out of scope**. Its `dustynv` CUDA base image expects to run as root — the
preinstalled CUDA/torch tree lives under `/root` and the container needs the GPU device nodes — and
it is a single-purpose LAN-only box (see "the LAN-only assumption" in
`jetson/docker-compose.example.yml`). Leave `jetson/docker-compose.example.yml` alone.

### PUID and PGID

The uid that matters is whichever one owns your library on the host, so it is a variable rather
than a baked-in constant. Both default to `1000`. Set them in the same `.env` that holds
`POSTGRES_PASSWORD`:

```bash
stat -c '%u:%g' /path/to/your/ebooks     # → e.g. 1000:1000
```

```ini
# .env, next to docker-compose.yml
PUID=1000
PGID=1000
```

Nothing else needs changing; the image itself already ends on a non-root `USER`, and this only
retargets which uid that is.

### The one-time ownership change on an existing install

The container can only write what the *host* directory's ownership lets it write. Everything under
`/data/app`, `/data/imports` and `/backups` was created by root while the container was root, so
on an install that predates this change those directories must be handed to `PUID:PGID` once.

Stop the stack first, then, from the directory holding your `docker-compose.yml` (substitute your
own paths — these are the four bind mount **sources** from your compose file):

```bash
docker compose down

sudo chown -R 1000:1000 ./data          # → /data/app  and /data/imports
sudo chown -R 1000:1000 /path/to/your/backups
# The library roots only need to be *writable*; if they are already owned by a
# normal user, use that uid as PUID instead of re-owning them.
sudo chown -R 1000:1000 /path/to/your/ebooks /path/to/your/audiobooks

docker compose up -d --build server web
```

If your library is large, the two library `chown` lines are the slow part — check the ownership
first (`stat -c '%u:%g'`) and skip them if they already match.

### What breaks if you skip it

Not everything fails the same way, and the distinction matters when you are deciding how urgent
this is:

| Directory | If it is still root-owned |
|---|---|
| app-data (`/data/app`) | **The server does not start.** It creates `logs/` under this path at import time; a failure there is fatal, and `docker compose logs server` shows a `PermissionError` before anything else runs. This is the one you cannot defer |
| import staging (`/data/imports`) | ACSM inbox scanning logs an error each pass; nothing else is affected |
| backups (`/backups`) | Backup runs fail with a permission error and are reported as failed in **System → Backups**. Existing backups stay readable, so restore still works |
| library roots | Reads, scans, transcription and sync all work. Only the writes fail — upload, cover extraction into the ebook, metadata write-back, format conversion — one action at a time, with an error on that action |

So a half-finished migration is a stack that runs and serves the library read-only, not an outage —
except for app-data, which has to be right before the first start.

Temp space is the other thing to know about. The image sets `TMPDIR=/data/app/tmp`, because
`/tmp` in the container is a small tmpfs and a spooled multi-GB audiobook upload would fill it.
`entrypoint.sh` creates that directory at start and logs a warning if it cannot, falling back to
`/tmp` — which is a useful early signal that the app-data ownership is wrong, since it appears
before the first upload does.

### Verifying it took

```bash
docker compose exec server id
docker compose exec web id
# → uid=1000 gid=1000 ...  (or your PUID/PGID)

docker inspect -f '{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}} {{.HostConfig.Memory}}' <server container>
# → [ALL] [no-new-privileges:true] 4294967296

curl -fsS http://localhost:8000/api/health
curl -fsSI http://localhost:3000/ | head -1
# → {"status":"healthy",...}  and  HTTP/1.1 200 OK
```

Then exercise the writes: upload a small ebook, let it extract a cover, and run a manual backup
from **System → Backups**. Those are the three paths that touch three different mounts.

### Rolling back

The hardening is entirely in the compose file, and the ownership change is harmless to leave in
place. To go back to root containers, delete the `user:`, `cap_drop:` and `security_opt:` keys from
the `server` and `web` services in your `docker-compose.yml` and recreate:

```bash
docker compose up -d --force-recreate server web
```

Root can write directories owned by uid 1000, so nothing has to be re-owned to roll back — and
nothing has to be re-owned to roll forward again afterwards.

## Secrets

Secrets are passed to the containers as **files**, not as environment variables (issue #180).
Anything in a container's `environment:` is printed in full by `docker inspect`, is readable in
`/proc/1/environ`, and is therefore available to every member of the `docker` group on the host and
to anything that reaches code execution inside the container. That covered the JWT signing key, the
credential encryption keys, and the Postgres password embedded in `DATABASE_URL` — the three values
that between them are the whole deployment.

`docker-compose.example.yml` declares a top-level `secrets:` block; compose mounts each file
read-only at `/run/secrets/<name>` inside the services that list it, and the environment carries
only the path:

```yaml
secrets:
  jwt_secret_key:
    file: ./secrets/jwt_secret_key
```

```yaml
    environment:
      - JWT_SECRET_KEY_FILE=/run/secrets/jwt_secret_key
```

### How `<NAME>_FILE` behaves

`server/config.py` reads every secret setting from either form, with these rules — all pinned by
`server/tests/test_secret_files.py`:

- **`<NAME>_FILE` wins** over a plain `<NAME>` that is also set. It is a settings source placed
  ahead of the environment source, so precedence is structural rather than an `if`.
- **A `<NAME>_FILE` that cannot be resolved is a startup error naming the variable.** Missing file,
  unreadable file, empty file — the server refuses to boot rather than fall back to the plain
  variable. A silent fallback is how a deployment ends up running on the shipped default secret and
  looking healthy. Empty counts as an error because an empty JWT key is not one of the known
  defaults and would sail straight past the startup guard.
- **One trailing newline (or CRLF) is stripped**, and nothing else. Every way of writing a secret to
  a file adds one; leading and interior whitespace could be part of the value, so it is left alone.

Compose mounts secret files mode `0444`, owned by root — readable by the unprivileged uid the
`server` container runs as (see "Running as a non-root user" above), which is what makes the two
changes compatible.

### What ships

| Secret | Services | Environment variable |
|---|---|---|
| `jwt_secret_key` | `server` | `JWT_SECRET_KEY_FILE` |
| `postgres_password` | `server`, `db` | `POSTGRES_PASSWORD_FILE` |
| `credential_enc_keys` | `server` | `CREDENTIAL_ENC_KEYS_FILE` |
| `database_url` *(optional, commented out)* | `server` | `DATABASE_URL_FILE` |

The Postgres password is one file read by two containers. The official `postgres` image supports
the `_FILE` convention natively, and the server no longer receives a `DATABASE_URL` at all: it
assembles `postgresql+asyncpg://booksync:<password>@db:5432/booksync` from that same secret, using
`POSTGRES_USER`/`POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB` (defaults matching the `db` service).
So the two can no longer drift apart. Set `DATABASE_URL` or `DATABASE_URL_FILE` explicitly only for
an external database — an explicit URL always wins over the assembled one.

`GOOGLE_BOOKS_API_KEY_FILE` and `ABS_API_TOKEN_FILE` exist too, for the same reason; neither ships
wired up because both are optional and the runtime Audiobookshelf token lives encrypted in the
database rather than in the environment.

### Creating the files on a new install

From the directory holding your `docker-compose.yml`. `umask 077` in the same shell as the
redirects is what makes each file readable only by you:

```bash
mkdir -p secrets && cd secrets
umask 077

python3 -c "import secrets; print(secrets.token_urlsafe(64))" > jwt_secret_key
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > postgres_password
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > credential_enc_keys

ls -l   # → -rw------- on all three
cd ..
```

No `cryptography` on the host? Generate the Fernet key inside the server image, which has it:

```bash
umask 077
docker compose run --rm --no-deps --entrypoint python server \
  -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
  > secrets/credential_enc_keys
```

`secrets/` is gitignored (`/secrets/*`, with a `!/secrets/README.md` negation); `secrets/README.md`
is the tracked description of what belongs there.

### Migrating an existing install

**Copy the existing values. Do not generate new ones.** Every file must end up holding the *same
value* the environment holds today:

- A new `credential_enc_keys` cannot decrypt what the old one encrypted. Every stored import-source
  credential — the Audible auth blob, the Audiobookshelf API token — becomes unreadable, and there
  is no way to re-encrypt them without the old key. Keeping the value means nothing is
  re-encrypted and nothing has to be re-entered.
- A new `jwt_secret_key` invalidates every access and refresh token in existence: every browser and
  every phone is signed out. Phones are usually the devices holding unsynced reading positions
  (`docs/position-sync-contract.md`), so this is worth avoiding, not just tidier.
- A new `postgres_password` does not reach the database at all — `POSTGRES_PASSWORD` is only
  applied on the first init of the data volume — so the server simply fails to authenticate.

The commands below read each value straight out of the running container into a file. Nothing is
printed to the terminal, so nothing lands in your shell history or scrollback.

```bash
# From the directory holding docker-compose.yml, with the stack still running.
mkdir -p secrets && cd secrets
umask 077

docker compose exec -T server printenv JWT_SECRET_KEY       > jwt_secret_key
docker compose exec -T server printenv CREDENTIAL_ENC_KEYS  > credential_enc_keys
docker compose exec -T db     printenv POSTGRES_PASSWORD    > postgres_password

# Sanity: three non-empty files, owner-only.
ls -l
wc -c jwt_secret_key credential_enc_keys postgres_password
cd ..
```

Confirm each file matches its live value without printing either:

```bash
docker compose exec -T server printenv JWT_SECRET_KEY      | diff -q - secrets/jwt_secret_key
docker compose exec -T server printenv CREDENTIAL_ENC_KEYS | diff -q - secrets/credential_enc_keys
docker compose exec -T db     printenv POSTGRES_PASSWORD   | diff -q - secrets/postgres_password
# no output from any of the three = identical
```

Then edit `docker-compose.yml` to match the template — the whole diff is:

1. **`server`**: delete the `DATABASE_URL=`, `JWT_SECRET_KEY=` and `CREDENTIAL_ENC_KEYS=` lines from
   `environment:`; add `JWT_SECRET_KEY_FILE=/run/secrets/jwt_secret_key`,
   `CREDENTIAL_ENC_KEYS_FILE=/run/secrets/credential_enc_keys` and
   `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`; add a `secrets:` list naming all three.
2. **`db`**: replace `POSTGRES_PASSWORD=...` with
   `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`; add a `secrets:` list naming
   `postgres_password`.
3. **Top level**: add the `secrets:` block with the three `file: ./secrets/<name>` entries.

Recreate both services:

```bash
docker compose up -d --force-recreate server db
```

Recreating `db` is safe and does not touch the data. `POSTGRES_PASSWORD` and
`POSTGRES_PASSWORD_FILE` are both read only when the image initialises an *empty* data directory;
on an existing `booksync_db` volume the entrypoint skips initialisation entirely and the stored
password is whatever `ALTER ROLE` last set it to. Switching to `POSTGRES_PASSWORD_FILE` therefore
cannot change the password — but the file still has to hold the same value, because the *server*
builds its connection URL from it. That is what the `diff -q` above verifies.

### Verifying

```bash
# 1. Health. The API port is not published, so ask through the web container.
curl -fsS http://localhost:3000/api/health
# → {"status":"healthy",...}

# 2. No secret left in the environment. Both should print 0.
docker inspect -f '{{json .Config.Env}}' <server container> | grep -c -E 'JWT_SECRET_KEY=|CREDENTIAL_ENC_KEYS=|DATABASE_URL=.*:.*@'
docker inspect -f '{{json .Config.Env}}' <db container>     | grep -c 'POSTGRES_PASSWORD='

# 3. The files are where the containers expect them.
docker compose exec server ls -l /run/secrets/
```

Then, in the web UI:

- **Log in.** A session that was already open must still work — that is the JWT key and the database
  password both proving themselves.
- **Open System → Import Sources.** An existing Audible or Audiobookshelf source that still shows as
  configured, and a "Test connection" that still succeeds, is the credential encryption key proving
  itself: that token was decrypted with the key from the file.

If the encryption key is wrong the symptom is specific and quiet — the source is still listed, but
its stored credential fails to decrypt and the server logs a decryption error on use. Check it
before you walk away.

### Rolling back

The change is entirely in `docker-compose.yml`; the secret files can stay where they are.
To roll back: put the three plain values back under `environment:` (they are the same values
the files hold), delete the `*_FILE` lines, the per-service `secrets:` lists and the top-level
`secrets:` block, and recreate:

```bash
docker compose up -d --force-recreate server db
```

Nothing is re-encrypted and no session is invalidated in either direction, precisely because the
values never changed. Delete `secrets/` only once you are sure you are not going forward again.

## Rotating the Postgres password

The password is only applied by Postgres on **first initialization** of the data volume — that is
true of `POSTGRES_PASSWORD` and `POSTGRES_PASSWORD_FILE` alike. On an existing deployment, editing
the secret file alone won't rotate the stored password; the `server` container will just fail to
authenticate. Rotate it in the database first:

```bash
docker compose exec db psql -U booksync -d booksync -c "ALTER ROLE booksync WITH PASSWORD 'your-new-generated-password';"
```

Then write the same new value into `secrets/postgres_password` (`umask 077` first, as in "Secrets"
above) and recreate the `server` service so it picks the file up:

```bash
docker compose up -d --force-recreate server
```

On the plain-`environment:` path this is instead "update `POSTGRES_PASSWORD` and `DATABASE_URL` in
`docker-compose.yml` to match". Either way it does not touch or wipe any data in the `booksync_db`
volume.

## Rotating credential encryption keys

`CREDENTIAL_ENC_KEYS` is a comma-separated list of Fernet keys. The first key encrypts new writes;
every key in the list is tried for decryption. So rotation is "prepend a new key and keep the old
one around" — no migration step, and no re-entering of import-source credentials.

Generate a new key with:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

The list lives in `secrets/credential_enc_keys` (see "Secrets" above): prepend the new key,
comma-separated, keep the old ones, and recreate the server —
`docker compose up -d --force-recreate server`.

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

The first account is the superadmin created on first boot. How anyone else gets one is the
**registration mode** below.

A waiting request shows as a count badge on the **System** entry in the sidebar and as a
"Pending User Requests" tile on the System status dashboard — both admin-only, because the count
comes from the admin user list. Approve or reject in System → User Management.

### Registration and invites

`registration_mode` is a system setting (System → Settings, or `PUT /api/settings/`), not an
environment variable, so changing it needs no redeploy. Three values:

| Mode | What a stranger at the login page gets |
|---|---|
| `open` | A "Request Access" form. The account is created inactive and an admin approves it. |
| `invite` | The same form plus a required **invite code** an admin generated. |
| `closed` | No form. One line: "Ask your administrator for an account." Admins create accounts in System → User Management. |

**What your server does after upgrading.** The mode is seeded once, at first boot after the
upgrade, from whether the database already has users: a server that already had accounts is
seeded `open`, so nothing about its behaviour changes; a brand new install is seeded `invite`.
Move an existing server to `invite` or `closed` yourself when you are ready — the seed never
overwrites a choice you have made.

`ALLOW_PUBLIC_REGISTRATION=false` still works and still wins: it forces `closed` whatever the
setting says. It can only ever be *more* restrictive, so it is safe to leave set on a host where
you want registration off no matter who edits the settings page.

**Invites** (System → User Management → Invites) are single-use and expire after
`invite_expiry_days` (7). Create one, copy the code, hand it to the person who should have an
account. The code is shown once and once only: the database stores a SHA-256 of it, so there is
nothing to show again and a copy of the table cannot be turned back into a working invite. Revoke
an unused one from the same screen. An invite is spent by the request that presents it — including
a request that collides with a username somebody already has, because leaving it usable would tell
whoever holds it that the name is taken. Reissue one if that happens.

**Why the register endpoint always says the same thing.** It answers `201 {"message": "Access
request submitted…"}` whether the request was accepted, collided with an existing username or
email, presented no invite code, presented a wrong or already-spent one, or arrived with the
pending queue full. Anything else would be an oracle for which accounts exist, which is what feeds
credential-stuffing runs against `/api/auth/login`. You see the truth: a real request appears in
the pending list, a collision does not, and a refusal is in the audit log
(`register_duplicate`, `invite_consumed`, `invite_created`, `invite_revoked` — never the code
itself). `closed` is the one mode that refuses outright, with a 403 that is identical for every
caller and so discloses nothing about accounts.

**Two ceilings**, both settings:

* `registration_pending_max` (20) — the number of *unapproved* accounts allowed to exist. Past it
  the endpoint answers exactly as it does on success and stores nothing, and logs one WARNING
  naming the count. Approve or reject the queue to clear it. This is the layer that bounds a
  flood, because it does not depend on the client address meaning anything.
* `registration_rate_limit` (5) per `registration_rate_window_seconds` (600) — a per-IP bucket on
  `POST /api/auth/register`, answering 429 with `Retry-After`. Behind a reverse proxy it is only
  as good as `FORWARDED_ALLOW_IPS` (see "Reverse proxy" above); without it the whole deployment
  shares one bucket, which for an endpoint a real person uses once is an acceptable ceiling
  rather than an outage.

Both the web login page and the Android sign-in screen read the mode from the unauthenticated
`GET /api/auth/registration` before showing anything, and both fall back to `open` if that call
fails — a transient 502 must not hide the request form from everyone.

Reading position, bookmarks and progress are **per user**. Two people using the same server keep
separate positions in the same book.

### Account deletion

Users delete their own accounts: **Account → Delete account**, in the Android app and in the web
app. It asks for the current password and for the word `DELETE` to be typed, then calls
`DELETE /api/auth/me`. Admins can still delete someone else's account from System → User
Management (`DELETE /api/users/{id}`).

Google Play requires this of any app that can create an account, and it requires a publicly
reachable page describing it as well — that page is **`/account-deletion`** on your server, served
by the web app without a login. It is the URL that goes in the Play Console's Data safety →
Data deletion field; see [play-listing.md](play-listing.md).

What a deletion removes: the user row, their bookmarks, each bookmark's change log and per-device
position hints, their progress rows, and every one of their `refresh_tokens` sessions — so no
other device is left holding a 30-day refresh token for an account that is gone. Nothing is
soft-deleted and nothing is recoverable without a backup restore.

What it keeps: the `audit_logs` row recording the deletion. `audit_logs.user_id` is
`ON DELETE SET NULL`, so the row survives with the actor redacted; `target_user_id` carries no
foreign key and keeps the number, which is what lets you answer "was this account deleted, or did
it never exist" afterwards. The action name is `account_self_deleted`.

Two refusals are deliberate:

* **The last active superadmin cannot delete themselves** (409). A server with no active
  superadmin cannot approve a registration, promote anyone or open the admin console, and nothing
  in the app can undo it — recovery would mean editing the database by hand. Promote someone else
  first.
* **A wrong password is refused** (403) and counted against the same per-user lockout as
  `POST /api/auth/change-password` (`password_change_failure_limit`). Both endpoints verify the
  current password, so they are one bcrypt oracle; a lockout on either locks both.

### Account recovery

There is **no self-service password reset** — no email is sent, no reset link exists. Recovery is
by role:

**An ordinary user** asks an admin. The admin opens System → User Management, uses **Reset
password**, and hands the generated password over. The account is flagged `must_reset_password`,
so the first thing it does at next sign-in is choose its own. The login screen says as much
("Forgot your password? Ask your administrator to reset it.") so nobody has to guess.

**A locked-out admin or the sole superadmin** has nobody above them, and the first-boot bootstrap
only creates an account on an *empty* database — it will not mint a second one to rescue you. Use
the break-glass script, which runs inside the server container against the configured
`DATABASE_URL`:

```bash
docker compose exec server python -m scripts.reset_password <username>
```

It prints one generated password. That password is a handover credential, not a chosen one: the
account must replace it at next sign-in. The script also bumps `token_version` and revokes every
live session (so a device that still holds a token is signed out), re-activates the account if it
had been deactivated, and writes a `password_reset` audit row with no acting user — a reset that
came from the container shell rather than from an admin in the UI is exactly what that row means.
The password itself is printed and nothing more; it is never written to the audit log or the
server log.

`--password` lets you supply one instead. Prefer the generated one — a password typed on the
command line lands in your shell history.

**Holding shell on the host is the authorisation.** That is why this is a script and not an
endpoint: an endpoint would need an authorisation story of its own, and every such story is
another way in. Self-service email reset is deliberately out of scope; if it is ever added it
needs a signed, single-use, short-TTL token and its own rate limit.

**This does not clear a login throttle.** The failed-attempt counters live in the server
process's memory (see "Login throttling" above), so a username sitting at the limit stays locked
until the window passes or the server restarts — the reset changes the password, not the bucket.

### Audit log retention

Security-relevant events (logins, failed logins, lockouts, role and password changes) go to the
`audit_logs` table, readable by admins in the user-management Audit Log tab. Each row holds a user id, the
client IP and a short description — on a failed login that description includes the username that
was typed — so it is the most personal table in every nightly dump.

Rows are deleted once they are older than **`audit_log_retention_days`, which defaults to 90**.
Set it to `0` to keep the log forever. The value lives in system settings (`PUT /api/settings/`,
admin only) and the prune runs on the backup scheduler's tick (every 10 minutes), so a change
takes effect without a restart. The `details` text is capped at 500 characters at write time.
