# Backup & Restore

Book Sync keeps a lot of state that **cannot be regenerated**: reading/listening
positions and history, sync maps (each is hours of GPU transcription), pairing and
unpair-exclusion decisions, user accounts, and the **encrypted import-source
credentials** (Audible auth blob, Audiobookshelf token, Adobe/DeACSM device
authorization). A dead disk or a stray `docker compose down -v` loses all of it.

The server itself runs backups: a nightly schedule, on-demand manual backups, retention
pruning, and restore — all managed from **System → Backups** in the web UI. This document
describes what's backed up, how to configure and monitor it, and how to restore (from the
UI and by hand).

## What is backed up

A complete, restorable deployment is three things:

| Part | Where it lives | Backed up by |
|------|----------------|--------------|
| **Database** | Postgres volume `booksync_db` — positions, sync maps, pairs, users, *encrypted* credential rows | nightly `pg_dump` (custom format) |
| **App data** | `./data` bind mount — `covers/` (regenerable but slow), `logs/`, `imports/` (throwaway) | nightly hardlink **snapshot** of `covers/` |
| **Secrets** | `credential_enc_keys`, `jwt_secret_key`, `postgres_password` — one value per file in the gitignored `secrets/` directory beside `docker-compose.yml` (see [operations.md](operations.md#secrets)) | **you** (password manager — see below) |

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

- [ ] `credential_enc_keys` (all keys, in order — the first encrypts, all decrypt)
- [ ] `jwt_secret_key`
- [ ] `postgres_password`

The `secrets/` directory is deliberately outside the backup archive: an archive that carried both
the encrypted rows and the key that decrypts them would not be encrypted at rest at all. Copy the
values into your password manager by hand.

## The backup engine

Backups are owned by the **server** process (`services/backup_service.py`) — there is no
separate backup container. A background scheduler task started at app lifespan:

1. Wakes periodically and, at the configured hour, takes the day's backup (idempotent —
   it won't run twice for the same day, and survives restarts).
2. Writes a custom-format dump `booksync-db-YYYY-MM-DD.dump` to `/backups`.
3. Snapshots covers to `/backups/covers/YYYY-MM-DD/` (skips logs and imports — see
   *Cover snapshots* below).
4. Prunes old **scheduled** backups: keeps the newest *keep-daily* plus 1st-of-month up to
   *keep-monthly*, for both dumps and cover snapshots. **Manual backups are never pruned.**

On a fresh deployment it takes one backup immediately, so there's something to restore
before the first scheduled run.

### Manual backups

From **System → Backups**, a superadmin can **Create backup** on demand (with an optional
label like *"before big reorg"*). Manual backups get a timestamped id
(`YYYY-MM-DD_HHMMSS-manual`) and are **kept indefinitely until you delete them** — retention
pruning ignores them. Each backup row also offers **Download** (saves the `.dump` to your
machine for off-site safekeeping) and **Delete**.

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
mounts) and `rsync` (installed in the server image). To eyeball the dedup:
`du -sh /backups/covers/*` shows later snapshots as small deltas, and `ls -li` shows
shared inode numbers for unchanged covers.

### Configuration

Schedule and retention are edited in **System → Backups** (stored in the database, so no
redeploy needed):

| Setting | Default | Meaning |
|---------|---------|---------|
| Nightly backups | on | Master on/off for the scheduled run |
| Hour (UTC) | `3` | Hour (0–23) of the daily run |
| Keep daily | `14` | Recent daily backups to keep |
| Keep monthly | `6` | 1st-of-month backups to keep beyond the daily window |

**Mount `/backups` at a NAS path OFF the docker host's disk** — a backup on the same disk
that dies is worthless. It is mounted **read-write into the `server` service** (the server
writes, lists, restores, and prunes):

```yaml
# server service
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

1. Open **System → Backups**. Admins see the location, last run, retention/schedule
   controls, and the list of available backups.
2. **Superadmins** get per-row **Restore / Download / Delete** and a **Create backup**
   button. To restore: click **Restore** on a row, type `RESTORE` to confirm, and confirm.
3. The server terminates other DB sessions, runs `pg_restore --clean --if-exists` over
   the live database, and copies that backup's covers snapshot into the live covers dir if
   present.
4. Reload the app afterward.

### By hand (host fallback)

If the UI is unavailable, restore directly against the stack. `<ID>` is the backup id — a
date (`YYYY-MM-DD`) for scheduled backups or `YYYY-MM-DD_HHMMSS-manual` for manual ones.

```bash
# 1. Database (custom-format dump → pg_restore). --clean --if-exists drops and
#    recreates objects, so this replaces the current schema and data wholesale.
docker compose cp /path/to/nas/booksync-backups/booksync-db-<ID>.dump db:/tmp/restore.dump
docker compose exec db pg_restore --clean --if-exists --no-owner --no-privileges \
  -U booksync -d booksync /tmp/restore.dump

# 2. Covers — copy that backup's snapshot into the app-data covers dir.
cp -a /path/to/nas/booksync-backups/covers/<ID>/. ./data/covers/

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
copy of `docker-compose.yml` with different ports/volume names, pointed at a `secrets/`
directory holding the **same** `credential_enc_keys` / `postgres_password` values), then verify:

- [ ] Login works.
- [ ] Reading/listening positions and history are intact.
- [ ] Covers render.
- [ ] Audible / ABS / ACSM import sources still show **authorized** (proves the Fernet
      keys decrypt the restored credential rows).
- [ ] A fresh dump appears on the NAS nightly (and on a **Create backup** click) and is a
      valid archive (`pg_restore -l booksync-db-<ID>.dump` lists its contents).
