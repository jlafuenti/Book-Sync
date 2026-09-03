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
