"""How this server treats a stranger who wants an account (issue #210).

Registration used to be one boolean, defaulted to on, with no ceiling and a
``409`` that told anyone which usernames and emails already existed. Three modes
replace it, stored in ``system_settings`` so an operator can change their mind
without a redeploy:

``open``    anyone may file a request; an admin approves it. The old behaviour.
``closed``  no self-registration at all; admins create the accounts.
``invite``  the request form only works with a code an admin generated — the
            default for a **new** install, because an internet-reachable server
            with an open request form collects junk, and the operator of a
            personal library knows every user by name.

**An existing database must not change behaviour because it was upgraded.**
:func:`seed_mode` runs once, before the superadmin bootstrap, and writes ``open``
when the database already has users and ``invite`` when it does not. So a server
that has been taking requests goes on taking them, and the operator moves it to
``invite`` deliberately, from the settings page.

``ALLOW_PUBLIC_REGISTRATION=false`` remains an override: it was the old off
switch, and an operator who set it must keep a closed server whatever the row
says. Nothing forces it *open*, so the env var can only ever be more restrictive
than the setting.

This module is deliberately import-light — models and the settings row, nothing
from ``routers`` — because both ``routers/auth.py`` and ``routers/settings.py``
import it, and those two already import each other in one direction.
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.settings import SystemSetting

logger = logging.getLogger(__name__)

MODE_OPEN = "open"
MODE_INVITE = "invite"
MODE_CLOSED = "closed"

#: Every value ``registration_mode`` may hold. Anything else is a 422 on the
#: settings PUT — a typo here would otherwise fall through to "not open", which
#: silently closes a server nobody meant to close.
MODES = (MODE_OPEN, MODE_INVITE, MODE_CLOSED)

#: Defaults for the registration keys in ``system_settings``. Mirrored into
#: ``routers/settings.DEFAULT_SETTINGS`` (which owns the type-casting of a stored
#: row) — this module owns their meaning.
DEFAULTS: Dict[str, Any] = {
    # New installs start on invites; see seed_mode for what an upgrade gets.
    "registration_mode": MODE_INVITE,
    # How long a new invite is good for.
    "invite_expiry_days": 7,
    # Ceiling on *unapproved* accounts. Past it the endpoint answers exactly as
    # it does on success and stores nothing: a stranger cannot tell a full queue
    # from an accepted request, and the admin's pending list cannot be flooded.
    "registration_pending_max": 20,
    # Per-IP bucket on POST /api/auth/register. Conservative on purpose — a real
    # person files one request, ever.
    "registration_rate_limit": 5,
    "registration_rate_window_seconds": 600,
}

#: Last values read from the database, so the synchronous rate-limit suppliers
#: in ``rate_limit.py`` have something to read. The register route refreshes this
#: (via :func:`load`) before it touches the bucket, so the values the bucket uses
#: are the ones that were on file at the start of the request being metered.
_live: Dict[str, Any] = dict(DEFAULTS)


def forget_cached_settings() -> None:
    """Drop the cache back to defaults. Test hook, and used by the settings PUT
    so an admin's change takes effect on the next request rather than the one
    after it."""
    _live.clear()
    _live.update(DEFAULTS)


def current(key: str) -> Any:
    """The last value seen for ``key``, or its default."""
    return _live.get(key, DEFAULTS[key])


def _coerce(key: str, raw: Optional[str]) -> Any:
    """A stored row is text; give it back the type its default has."""
    if raw is None:
        return DEFAULTS[key]
    default = DEFAULTS[key]
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


async def load(db: AsyncSession) -> Dict[str, Any]:
    """Read every registration key from the database and refresh the cache."""
    rows = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(tuple(DEFAULTS)))
        )
    ).scalars().all()
    stored = {row.key: row.value for row in rows}
    values = {key: _coerce(key, stored.get(key)) for key in DEFAULTS}
    if values["registration_mode"] not in MODES:
        values["registration_mode"] = DEFAULTS["registration_mode"]
    _live.update(values)
    return values


async def stored_mode(db: AsyncSession) -> str:
    """The mode on file, ignoring the env override."""
    return (await load(db))["registration_mode"]


async def effective_mode(db: AsyncSession) -> str:
    """What the server actually does, env override included."""
    from config import settings

    if not settings.allow_public_registration:
        return MODE_CLOSED
    return await stored_mode(db)


async def seed_mode(db: AsyncSession) -> str:
    """Give a database its first ``registration_mode``, once.

    Called from the lifespan **before** ``bootstrap_superadmin`` — which creates
    the first account on a fresh install, and would otherwise make every database
    look like one that already has users.

    Returns the mode in force afterwards. A row that already exists is never
    overwritten: this is a seed, not a policy.
    """
    from models.user import User

    existing = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.key == "registration_mode")
        )
    ).scalar_one_or_none()
    if existing is not None and existing.value in MODES:
        return existing.value

    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    mode = MODE_OPEN if user_count else MODE_INVITE
    if existing is None:
        db.add(SystemSetting(key="registration_mode", value=mode))
    else:
        existing.value = mode
    await db.commit()
    forget_cached_settings()
    logger.info(
        "Seeded registration_mode=%s (%d existing user(s)). Change it in "
        "System settings; see docs/operations.md, \"Registration and invites\".",
        mode,
        user_count,
    )
    return mode


def validate_settings(new_settings: Dict[str, Any]) -> None:
    """Reject a bad registration setting before anything is written.

    Raises ``ValueError`` with a message the settings PUT turns into a 422.
    """
    if "registration_mode" in new_settings:
        mode = str(new_settings["registration_mode"]).strip().lower()
        if mode not in MODES:
            raise ValueError(
                f"registration_mode must be one of: {', '.join(MODES)}"
            )
        new_settings["registration_mode"] = mode

    for key in (
        "invite_expiry_days",
        "registration_pending_max",
        "registration_rate_limit",
        "registration_rate_window_seconds",
    ):
        if key not in new_settings:
            continue
        try:
            value = int(new_settings[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number")
        floor = 0 if key == "registration_pending_max" else 1
        if value < floor:
            raise ValueError(f"{key} must be at least {floor}")
        new_settings[key] = value
