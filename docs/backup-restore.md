# Backup & Restore

Book Sync keeps a lot of state that **cannot be regenerated**: reading/listening
positions and history, sync maps (each is hours of GPU transcription), pairing and
unpair-exclusion decisions, user accounts, and the **encrypted import-source
credentials** (Audible auth blob, Audiobookshelf token, Adobe/DeACSM device
authorization). A dead disk or a stray `docker compose down -v` loses all of it.

This document describes the nightly backup sidecar, how to monitor it, and how to
restore — both from the web UI and by hand.

## What is backed up

A complete, restorable deployment is three things:

| Part | Where it lives | Backed up by |
|------|----------------|--------------|
| **Database** | Postgres volume `booksync_db` — positions, sync maps, pairs, users, *encrypted* credential rows | nightly `pg_dump` (custom format) |
| **App data** | `./data` bind mount — `covers/` (regenerable but slow), `logs/`, `imports/` (throwaway) | nightly hardlink **snapshot** of `covers/` |
| **Secrets** | `CREDENTIAL_ENC_KEYS`, `JWT_SECRET_KEY`, `POSTGRES_PASSWORD` — only in your gitignored `docker-compose.yml` | **you** (password manager — see below) |

**Not backed up (regenerable):** Whisper models, `node_modules`/build artifacts.

**Media files** (`/data/ebooks`, `/data/audiobooks`) are your own host paths. They are
assumed to already live on the NAS with its own redundancy — confirm that and treat it
as out of scope for these backups.

> ⚠️ **The Fernet keys are the catch.** The credential rows in a database dump are
> encrypted at rest with `CREDENTIAL_ENC_KEYS`. **Restoring a dump without the matching
> keys leaves those rows undecryptable** — you would have to re-link Audible / ABS /
> Adobe from scratch, and **re-authorizing Adobe burns one of your limited device
> activation slots**. Back the keys up separately (next section).

## Secrets checklist (do this once)

Store these in your password manager — they are **not** in the database dump:

- [ ] `CREDENTIAL_ENC_KEYS` (all keys, in order — the first encrypts, all decrypt)
- [ ] `JWT_SECRET_KEY`
- [ ] `POSTGRES_PASSWORD`

## The backup sidecar

`docker-compose.example.yml` defines a `backup` service (a `postgres:16-alpine`
container running [`scripts/backup.sh`](../scripts/backup.sh)). It:

1. Sleeps until `BACKUP_HOUR` each day (a self-scheduling loop — no cron).
2. Writes a custom-format dump `booksync-db-YYYY-MM-DD.dump` to `/backups`.
3. Snapshots covers to `/backups/covers/YYYY-MM-DD/` (skips logs and imports — see
   *Cover snapshots* below).
4. Prunes old backups: keeps the newest `BACKUP_KEEP_DAILY` daily plus 1st-of-month
   up to `BACKUP_KEEP_MONTHLY`, for both DB dumps and cover snapshots.

On a fresh deployment it also takes one backup immediately, so there's something to
restore before the first scheduled run.

### Cover snapshots (hardlinked, space-efficient)

Covers are append-only and regenerable, so re-copying ~hundreds of MB every night would
be wasteful. Instead each night is a **hardlink snapshot** (the `rsync --link-dest` /
Time Machine technique): `covers/<date>/` looks like a full directory, but files
unchanged since the previous snapshot are hardlinks sharing the same inodes. A day
therefore costs only the *new* covers — 15 snapshots ≈ one full copy plus deltas.

Pruning the oldest snapshot is a plain `rm -rf covers/<date>/`; because each snapshot is
an independent hardlink tree, the filesystem's inode refcount reclaims only the blocks no
other snapshot references — there's no incremental chain to replay, and no base you can't
delete.

This needs a filesystem that supports hardlinks (local ext4/xfs — **not** most CIFS/SMB
mounts) and `rsync` (the sidecar installs it at startup; without it, it falls back to full
per-night copies). To eyeball the dedup: `du -sh /backups/covers/*` shows later snapshots
as small deltas, and `ls -li` shows shared inode numbers for unchanged covers.

### Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `BACKUP_HOUR` | `3` | Local hour (0–23) of the daily run |
| `BACKUP_KEEP_DAILY` | `14` | Recent daily dumps to keep |
| `BACKUP_KEEP_MONTHLY` | `6` | 1st-of-month dumps to keep beyond the daily window |

**Mount `/backups` at a NAS path OFF the docker host's disk** — a backup on the same
disk that dies is worthless. The same path is mounted **read-only into the `server`
service** so the web UI can list backups and restore. Both mounts must point at the
same directory:

```yaml
# server service
  - /path/to/nas/booksync-backups:/backups:ro
# backup service
  - /path/to/nas/booksync-backups:/backups
```

### Sizing

Dumps are metadata only (no media): typically tens of MB even with many sync maps.
Cover snapshots share storage via hardlinks, so ~20 retained snapshots cost roughly one
full copy of `covers/` plus the day-to-day deltas — not 20× the covers size.

## Monitoring

`GET /api/stats/backup` (any logged-in user) reports the last successful run:

```json
{ "configured": true, "location": "/backups",
  "last_backup_utc": "2026-07-13T03:00:11Z", "age_seconds": 120,
  "stale": false, "latest_db_file": "booksync-db-2026-07-13.dump",
  "latest_db_size_bytes": 12345678 }
```

`stale` becomes `true` when the newest dump is older than 36 hours (i.e. a nightly run
was missed) or when no backups exist. The **System** page surfaces this as an **Overdue**
badge next to the last-run time.

## Restore

Restoring **overwrites the live database and covers** and briefly disrupts the app.

### From the web UI (superadmin)

1. Open **System → Backups**. Admins see the location, last run, and the list of
   available backups.
2. **Superadmins** additionally get a **Restore…** control. Select a backup, click
   **Restore…**, type `RESTORE` to confirm, and confirm.
3. The server terminates other DB sessions, runs `pg_restore --clean --if-exists` over
   the live database, and copies that date's covers snapshot into the live covers dir if
   present.
4. Reload the app afterward.

### By hand (host fallback)

If the UI is unavailable, restore directly against the stack. `<DATE>` is the backup id
(`YYYY-MM-DD`).

```bash
# 1. Database (custom-format dump → pg_restore). --clean --if-exists drops and
#    recreates objects, so this replaces the current schema and data wholesale.
docker compose cp /path/to/nas/booksync-backups/booksync-db-<DATE>.dump db:/tmp/restore.dump
docker compose exec db pg_restore --clean --if-exists --no-owner --no-privileges \
  -U booksync -d booksync /tmp/restore.dump

# 2. Covers — copy that date's snapshot into the app-data covers dir.
cp -a /path/to/nas/booksync-backups/covers/<DATE>/. ./data/covers/

# 3. Restart the app so it reconnects with a clean pool.
docker compose restart server
```

> **pg_restore vs Alembic:** a full custom-format dump already contains the schema, so
> `pg_restore --clean` recreates it. Do **not** also run `alembic upgrade head` against a
> just-restored database expecting it to build the schema — the dump is from a specific
> revision and is already at that state. If you restore into a truly empty database via a
> different path, `alembic stamp head` marks it current without re-running migrations.

## Test-restore drill (do this at least once)

An untested backup is a hope, not a backup. Restore into a **scratch** compose stack (a
copy of `docker-compose.yml` with different ports/volume names and the **same**
`CREDENTIAL_ENC_KEYS` / `POSTGRES_PASSWORD`), then verify:

- [ ] Login works.
- [ ] Reading/listening positions and history are intact.
- [ ] Covers render.
- [ ] Audible / ABS / ACSM import sources still show **authorized** (proves the Fernet
      keys decrypt the restored credential rows).
- [ ] A fresh dump appears on the NAS nightly and is a valid archive
      (`pg_restore -l booksync-db-<DATE>.dump` lists its contents).
