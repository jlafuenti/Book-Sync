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

## Storage

| Mount | Grows with | Notes |
|---|---|---|
| ebooks / audiobooks | your library | Read-only as far as the server is concerned, apart from format conversion writing an `.epub` sibling |
| `/data/app` | covers, working files | Extracted cover art accumulates; Troubleshoot can delete orphaned covers |
| `/data/imports` | ACSM import staging | Inbox / processed / failed subfolders |
| `/backups` | nightly dumps | Retention is configurable in System → Backups. **Put this on a different disk than the DB** |

## Users

The first account is the superadmin created on first boot. Additional users register themselves
(`ALLOW_PUBLIC_REGISTRATION`, on by default) but land inactive until an admin approves them — set
`ALLOW_PUBLIC_REGISTRATION=false` to close registration entirely.

Reading position, bookmarks and progress are **per user**. Two people using the same server keep
separate positions in the same book.
