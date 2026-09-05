#!/bin/sh
# Container entrypoint: apply DB migrations, then start the API.
#
# Schema management moved from a boot-time ALTER block to Alembic (issue #53).
# The DB service is guaranteed healthy before this runs (compose
# `depends_on: condition: service_healthy`), so the upgrade is safe here.
#
# NOTE: an existing database that predates Alembic must be stamped ONCE before
# the first deploy of this image, otherwise `upgrade head` tries to CREATE
# already-existing tables and fails:
#     alembic stamp head
# See docs/testing.md / the issue #53 notes.
set -e

# Scratch space (issue #180). server/Dockerfile sets TMPDIR into the app-data
# volume so a spooled multi-GB upload doesn't land on the container's small /tmp
# tmpfs. /data/app is a bind mount, so the directory baked into the image is
# hidden by it and has to be created here — by the unprivileged runtime uid,
# which makes this the earliest, clearest signal that the mount's ownership does
# not match PUID:PGID. Falling back to /tmp keeps ordinary requests working
# while the operator fixes it, instead of failing every upload with EACCES.
#
# Nothing in this script needs root: no chown, no privilege drop, just mkdir.
TMPDIR="${TMPDIR:-/tmp}"
if mkdir -p "$TMPDIR" 2>/dev/null && [ -w "$TMPDIR" ]; then
    export TMPDIR
else
    echo "[entrypoint] WARNING: TMPDIR=$TMPDIR is not writable by uid $(id -u)."
    echo "[entrypoint] Falling back to /tmp; large uploads and ebook conversions"
    echo "[entrypoint] may fail. Ownership of the app-data mount must match the"
    echo "[entrypoint] container's PUID:PGID — see docs/operations.md,"
    echo "[entrypoint] \"Running as a non-root user\"."
    TMPDIR=/tmp
    export TMPDIR
fi

echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
alembic upgrade head

# SINGLE PROCESS ONLY (issue #252). Do not add `--workers`, do not set
# WEB_CONCURRENCY, and do not run a second replica of this container. The
# transcription queue claims work with an unlocked SELECT-then-UPDATE, holds
# cancel/pause state in module-level Python sets, re-queues every in_progress
# row at startup, and starts the import and backup schedulers per process — so a
# second worker transcribes the same audiobook twice, ignores half the cancels,
# re-queues the other worker's live job and doubles the nightly backup.
# `check_single_process()` in server/config.py refuses to boot if the
# environment asks for more than one worker; scale by giving this process more
# CPU. See docs/operations.md, "Single process only".
echo "[entrypoint] Starting uvicorn (single process)..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
