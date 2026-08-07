"""
Off-hours transcription window (issue #106).

The transcription queue accepts work around the clock but may only *dispatch*
it inside a configured wall-clock window — the Jetson's GPU is shared with a
latency-sensitive voice pipeline the rest of the day.

Everything here is a pure function over an injected ``now`` so the window logic
is testable without a clock or a database; :func:`load_config` is the only
DB-touching entry point.

**Why an explicit IANA zone rather than the container's local time:** the server
container has no ``TZ`` set (the backup scheduler treats its hour as UTC), so
"01:00" would silently mean something different from what the admin typed.
Storing a zone name also keeps the window anchored to wall-clock time across DST
transitions, which a fixed UTC offset cannot do.

**Known DST approximation:** if ``start`` falls in a spring-forward gap (e.g.
02:30 on a US transition day), :func:`next_open` resolves it with Python's
``fold=0`` interpretation, so the *displayed* "opens at" may be an hour off for
that one night. The gate itself (:func:`in_window`) compares time-of-day and is
unaffected.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("offhours")

# Setting keys owned by this module. Defaults mirror routers/settings.py
# DEFAULT_SETTINGS — keep the two in sync.
ENABLED_KEY = "transcription_offhours_enabled"
START_KEY = "transcription_offhours_start"
END_KEY = "transcription_offhours_end"
TIMEZONE_KEY = "transcription_offhours_timezone"

DEFAULTS = {
    ENABLED_KEY: False,
    START_KEY: "01:00",
    END_KEY: "07:00",
    TIMEZONE_KEY: "UTC",
}

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class OffHoursConfig:
    """A resolved window. ``enabled=False`` means "dispatch anything, any time"."""

    enabled: bool
    start: datetime.time
    end: datetime.time
    tz: ZoneInfo

    @property
    def label(self) -> str:
        return f"{format_hhmm(self.start)}–{format_hhmm(self.end)} {self.tz.key}"


# ---------------------------------------------------------------------------
# Parsing / formatting
# ---------------------------------------------------------------------------

def parse_hhmm(raw) -> datetime.time:
    """Parse a ``HH:MM`` string into a time. Raises ValueError on anything else."""
    if not isinstance(raw, str):
        raise ValueError(f"Expected a HH:MM string, got {type(raw).__name__}")
    m = _HHMM_RE.match(raw.strip())
    if not m:
        raise ValueError(f"Invalid time '{raw}' — expected HH:MM (00:00–23:59)")
    return datetime.time(int(m.group(1)), int(m.group(2)))


def format_hhmm(t: datetime.time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def parse_timezone(raw) -> ZoneInfo:
    """Resolve an IANA zone name. Raises ValueError if the system can't find it."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Timezone must be an IANA name such as America/New_York")
    try:
        return ZoneInfo(raw.strip())
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"Unknown timezone '{raw}': {e}") from e


# ---------------------------------------------------------------------------
# Window math
# ---------------------------------------------------------------------------

def in_window(t: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    """
    Is time-of-day ``t`` inside ``[start, end)``?

    ``start > end`` means the window crosses midnight (22:00–06:00). Equal
    bounds are ambiguous — "always" and "never" are equally defensible readings
    — so they're rejected here and at settings validation rather than guessed.
    """
    if start == end:
        raise ValueError("Off-hours window start and end must differ")
    if start < end:
        return start <= t < end
    return t >= start or t < end


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _local(config: OffHoursConfig, now: Optional[datetime.datetime]) -> datetime.datetime:
    now = now or _now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(config.tz)


def is_open(config: OffHoursConfig, now: Optional[datetime.datetime] = None) -> bool:
    """True when work may be dispatched (always true when the window is off)."""
    if not config.enabled:
        return True
    return in_window(_local(config, now).time(), config.start, config.end)


def dispatch_allowed(
    config: OffHoursConfig, now: Optional[datetime.datetime] = None
) -> tuple[bool, str]:
    """``(allowed, reason)``. ``reason`` is queue-item message copy when blocked."""
    if is_open(config, now):
        return True, ""
    return False, (
        f"Waiting for off-hours window (opens {format_hhmm(config.start)} "
        f"{config.tz.key})"
    )


def _next_local_at(
    config: OffHoursConfig, now: Optional[datetime.datetime], at: datetime.time
) -> datetime.datetime:
    """The next instant whose local time-of-day is ``at`` (now counts as next)."""
    local = _local(config, now)
    candidate = datetime.datetime.combine(local.date(), at, tzinfo=config.tz)
    if candidate < local:
        # Wall-clock arithmetic: "same time tomorrow", offset recomputed on use.
        candidate += datetime.timedelta(days=1)
    return candidate


def next_open(
    config: OffHoursConfig, now: Optional[datetime.datetime] = None
) -> datetime.datetime:
    """The next instant the window opens, as a tz-aware datetime in ``config.tz``."""
    return _next_local_at(config, now, config.start)


def next_close(
    config: OffHoursConfig, now: Optional[datetime.datetime] = None
) -> datetime.datetime:
    """The next instant the window closes, as a tz-aware datetime in ``config.tz``."""
    return _next_local_at(config, now, config.end)


def seconds_until_open(
    config: OffHoursConfig, now: Optional[datetime.datetime] = None
) -> int:
    """Seconds to wait before dispatch is allowed again; 0 if it already is."""
    if is_open(config, now):
        return 0
    now = now or _now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return max(0, int((next_open(config, now) - now).total_seconds()))


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------

DISABLED = OffHoursConfig(
    enabled=False,
    start=parse_hhmm(DEFAULTS[START_KEY]),
    end=parse_hhmm(DEFAULTS[END_KEY]),
    tz=ZoneInfo(DEFAULTS[TIMEZONE_KEY]),
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def config_from_values(values: dict) -> OffHoursConfig:
    """
    Build a config from raw setting values (strings as stored in the DB).

    Any problem — unparseable time, equal bounds, unknown zone — degrades to
    :data:`DISABLED` with a warning rather than raising. A typo in a settings
    row must never wedge the queue loop; the worst case is that transcription
    behaves the way it did before the feature existed.
    """
    try:
        enabled = _as_bool(values.get(ENABLED_KEY, DEFAULTS[ENABLED_KEY]))
        if not enabled:
            return DISABLED
        start = parse_hhmm(values.get(START_KEY) or DEFAULTS[START_KEY])
        end = parse_hhmm(values.get(END_KEY) or DEFAULTS[END_KEY])
        if start == end:
            raise ValueError("Off-hours window start and end must differ")
        tz = parse_timezone(values.get(TIMEZONE_KEY) or DEFAULTS[TIMEZONE_KEY])
        return OffHoursConfig(enabled=True, start=start, end=end, tz=tz)
    except ValueError as e:
        logger.warning(
            "Off-hours settings are invalid (%s) — treating the window as disabled", e
        )
        return DISABLED


async def load_config(db=None) -> OffHoursConfig:
    """Read the window out of ``system_settings``. Never raises."""
    keys = (ENABLED_KEY, START_KEY, END_KEY, TIMEZONE_KEY)

    async def _read(session) -> dict:
        from sqlalchemy import select

        from models.settings import SystemSetting

        result = await session.execute(
            select(SystemSetting).where(SystemSetting.key.in_(keys))
        )
        return {s.key: s.value for s in result.scalars().all()}

    try:
        if db is not None:
            values = await _read(db)
        else:
            from database import async_session

            async with async_session() as session:
                values = await _read(session)
    except Exception as e:  # pragma: no cover — DB down; caller must keep running
        logger.warning("Could not load off-hours settings (%s) — window disabled", e)
        return DISABLED

    return config_from_values(values)


def validate_settings(payload: dict, stored: Optional[dict] = None) -> None:
    """
    Validate the off-hours keys in a settings PUT. Raises ValueError with a
    user-facing message; the router turns that into a 400.

    ``stored`` supplies the currently-persisted values so a partial update
    (changing only ``start``) is still checked against the real ``end`` — that's
    the only way ``start == end`` can sneak in.
    """
    touched = {k for k in (START_KEY, END_KEY, TIMEZONE_KEY, ENABLED_KEY) if k in payload}
    if not touched:
        return

    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (stored or {}).items() if k in DEFAULTS})
    merged.update({k: v for k, v in payload.items() if k in DEFAULTS})

    start = parse_hhmm(merged[START_KEY])
    end = parse_hhmm(merged[END_KEY])
    if start == end:
        raise ValueError(
            "Off-hours window start and end must differ — "
            "use 00:00–23:59 for an all-day window."
        )
    parse_timezone(merged[TIMEZONE_KEY])
