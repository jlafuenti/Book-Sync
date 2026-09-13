"""
Whether a newer Tandem release has been published (issue #463).

A running server had no way to tell its operator it was out of date. This asks
GitHub for the project's latest *release* and compares its semantic version with
`version.APP_VERSION` — the same approach Paperless-ngx and Audiobookshelf take,
and the one `docs/releasing.md` already sets up: one version for server, web and
Android, `vX.Y.Z` tags, and GitHub Releases carrying the changelog.

Comparing releases rather than commits matters. A release is itself the
deliberate "worth updating to" signal; a commit count against `main` would light
up for every Android-only or docs-only change and train operators to ignore it.

**Opt-in.** When enabled, the server contacts api.github.com, which sees the
server's address and nothing else. `docs/privacy.md` promises the server makes no
outbound call nobody asked for, so disabled means no request is made at all.

**Notify-only.** There is no "update now": doing it from inside the app would mean
handing the server container the Docker socket, undoing the non-root hardening of
issue #180 on the one process that faces the network.

Runs as a scheduler beside `backup_service` and `import_scheduler`, in the same
shape. The server is single-process (`config.check_single_process`), so the
module-level result is authoritative.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Optional

import httpx
from packaging.version import InvalidVersion, Version
from sqlalchemy import select

from database import async_session
from models.settings import SystemSetting
from utils import utcnow
from version import APP_VERSION

logger = logging.getLogger(__name__)

RELEASES_LATEST_URL = "https://api.github.com/repos/jlafuenti/Book-Sync/releases/latest"

# Releases are infrequent, and GitHub's unauthenticated limit is 60 requests an
# hour per address. Six hours is well inside both.
POLL_INTERVAL_SECONDS = 6 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 10

# Owned here; `routers/settings.py` spreads these into DEFAULT_SETTINGS.
DEFAULTS: dict[str, bool] = {
    "update_check_enabled": False,
    # Whether the admin has answered "check for updates automatically?". Two
    # booleans rather than one tri-state, because settings are coerced by the
    # type of their default and a nullable bool has no default type.
    "update_check_prompted": False,
}

Status = Literal["available", "current", "unknown"]
Reason = Literal[
    "disabled",
    "not_checked_yet",
    "no_releases",
    "rate_limited",
    "unreachable",
    "unrecognised_version",
]


def running_version() -> str:
    return APP_VERSION


def _parse(version: Optional[str]) -> Optional[Version]:
    if not version:
        return None
    text = version.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    try:
        return Version(text)
    except InvalidVersion:
        return None


def update_status(running: str, latest_tag: Optional[str]) -> Status:
    """Whether [latest_tag] is a newer release than [running].

    Versions compare numerically (`0.10.0` is newer than `0.9.0`, which a string
    comparison gets backwards). A pre-release is never offered, and anything that
    does not parse is `unknown` — a guess here would put a false banner in front
    of an operator.
    """
    latest = _parse(latest_tag)
    current = _parse(running)
    if latest is None or current is None:
        return "unknown"
    if latest.is_prerelease or latest.is_devrelease:
        return "unknown"
    return "available" if latest > current else "current"


def _now_iso() -> str:
    """UTC, `Z`-suffixed — the same shape `backup_service` reports `last_backup_utc` in.

    Built on `utils.utcnow()` per the repo's timestamp convention
    (`tests/test_time_contract.py`), even though this value never reaches a
    DateTime column.
    """
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _initial_state() -> dict[str, Any]:
    return {
        "status": "unknown",
        "reason": "not_checked_yet",
        "latest_version": None,
        "release_url": None,
        "checked_at": None,
    }


_state: dict[str, Any] = _initial_state()


async def is_enabled(db) -> bool:
    row = (await db.execute(
        select(SystemSetting).where(SystemSetting.key == "update_check_enabled")
    )).scalar_one_or_none()
    if row is None or row.value is None:
        return DEFAULTS["update_check_enabled"]
    return str(row.value).lower() == "true"


async def read_settings(db) -> dict[str, bool]:
    rows = (await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(list(DEFAULTS)))
    )).scalars().all()
    stored = {r.key: r.value for r in rows}
    return {
        key: default if stored.get(key) is None else str(stored[key]).lower() == "true"
        for key, default in DEFAULTS.items()
    }


def _record_failure(reason: Reason) -> None:
    """Note a failed check without erasing a good earlier result.

    A blip on GitHub's side would otherwise take down a known "update available"
    banner and put it back hours later, which reads as the release being withdrawn.
    """
    _state["checked_at"] = _now_iso()
    if _state["status"] in ("available", "current"):
        return
    _state["status"] = "unknown"
    _state["reason"] = reason


async def check_now() -> None:
    """Ask GitHub once and record the answer. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                RELEASES_LATEST_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
    except httpx.HTTPError as e:
        logger.info(f"[update-check] GitHub unreachable: {e}")
        _record_failure("unreachable")
        return

    if response.status_code == 404:
        # GitHub's answer until the first release is published.
        _record_failure("no_releases")
        return
    if response.status_code in (403, 429):
        _record_failure("rate_limited")
        return
    if response.status_code != 200:
        logger.info(f"[update-check] GitHub answered {response.status_code}")
        _record_failure("unreachable")
        return

    try:
        body = response.json()
        tag = body.get("tag_name") if isinstance(body, dict) else None
        url = body.get("html_url") if isinstance(body, dict) else None
    except ValueError:
        _record_failure("unreachable")
        return

    status = update_status(APP_VERSION, tag)
    if status == "unknown":
        _record_failure("unrecognised_version")
        return

    parsed = _parse(tag)
    _state.update({
        "status": status,
        "reason": None,
        "latest_version": str(parsed) if parsed is not None else None,
        "release_url": url,
        "checked_at": _now_iso(),
    })


def get_status(enabled: bool, prompted: bool) -> dict[str, Any]:
    """What the System page is told. A disabled check reports nothing it learned."""
    base = {
        "enabled": enabled,
        "prompted": prompted,
        "running_version": APP_VERSION,
    }
    if not enabled:
        return {
            **base,
            "status": "unknown",
            "reason": "disabled",
            "latest_version": None,
            "release_url": None,
            "checked_at": None,
        }
    return {**base, **_state}


def kick() -> asyncio.Task:
    """Run one check in the background — for the moment an admin enables it.

    Without this they would wait up to POLL_INTERVAL_SECONDS to learn it works,
    and the settings request must not wait on GitHub either.
    """
    return asyncio.create_task(check_now())


async def _tick() -> None:
    async with async_session() as db:
        enabled = await is_enabled(db)
    if enabled:
        await check_now()


_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


async def _run_loop() -> None:  # pragma: no cover — asyncio task plumbing (mirrors backup_service)
    logger.info("[update-check] scheduler started")
    while not _stop.is_set():
        try:
            await _tick()
        except Exception as e:
            logger.exception(f"[update-check] loop error: {e}")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("[update-check] scheduler stopped")


async def start() -> None:  # pragma: no cover — asyncio task plumbing
    global _task
    _stop.clear()
    if _task is None or _task.done():
        _task = asyncio.create_task(_run_loop())


async def stop() -> None:  # pragma: no cover — asyncio task plumbing
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except asyncio.TimeoutError:
            _task.cancel()
