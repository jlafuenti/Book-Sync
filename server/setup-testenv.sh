#!/usr/bin/env bash
# Create (or refresh) the local Python test environment for the backend.
#
#   cd server && ./setup-testenv.sh
#
# Idempotent — safe to re-run after pulling dependency changes. Every git
# worktree gets its own `server/.venv`; `uv` hardlinks packages from its global
# cache, so the second and later worktrees cost seconds and little disk.
#
# Why this exists: CI runs Python 3.12, and a global interpreter of some other
# version with unpinned pytest (or missing prod deps like `audible`) produces
# failures that don't exist in CI. The version lives in `.python-version`, which
# is committed, so a fresh clone or worktree provisions the right interpreter
# without anyone remembering a number.
set -euo pipefail

cd "$(dirname "$0")"

PYVER="$(tr -d '[:space:]' < .python-version)"

if command -v uv >/dev/null 2>&1; then
    # uv reads .python-version and downloads that CPython if it isn't installed.
    # --allow-existing makes a re-run reuse the venv instead of erroring; the
    # `uv pip install` below then refreshes packages in place.
    uv venv --python "$PYVER" --allow-existing .venv
    VENV_PY=".venv/Scripts/python.exe"
    [ -x "$VENV_PY" ] || VENV_PY=".venv/bin/python"
    VIRTUAL_ENV=.venv uv pip install -r requirements.txt -r requirements-dev.txt
elif command -v "py" >/dev/null 2>&1 && py "-$PYVER" --version >/dev/null 2>&1; then
    py "-$PYVER" -m venv --upgrade-deps .venv
    VENV_PY=".venv/Scripts/python.exe"
    [ -x "$VENV_PY" ] || VENV_PY=".venv/bin/python"
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r requirements.txt -r requirements-dev.txt
elif command -v "python$PYVER" >/dev/null 2>&1; then
    "python$PYVER" -m venv .venv
    VENV_PY=".venv/bin/python"
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r requirements.txt -r requirements-dev.txt
else
    # Deliberately fail rather than fall back to whatever `python` happens to be:
    # a venv built on the wrong version is the exact problem this script prevents,
    # and it would fail confusingly later instead of clearly here.
    echo "error: need Python $PYVER, and neither uv nor a $PYVER interpreter was found." >&2
    echo "Install uv (it will fetch $PYVER for you):" >&2
    echo "  winget install astral-sh.uv       # Windows" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux" >&2
    exit 1
fi

echo
echo "Ready. Run the suite with:"
echo "  cd server && .venv/Scripts/python.exe -m pytest -q     # Windows"
echo "  cd server && .venv/bin/python -m pytest -q             # macOS/Linux"
