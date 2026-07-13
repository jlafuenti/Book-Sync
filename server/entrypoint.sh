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

echo "[entrypoint] Starting uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
