"""
Off-hours transcription window math (issue #106, component 1).

The window is a wall-clock time-of-day range in an explicit IANA timezone, so
these are pure functions over an injected `now` — no DB, no real clock. The
DB-backed loader is covered at the bottom.

DST is deliberately exercised: the whole point of storing an IANA zone rather
than a UTC offset is that "01:00–07:00 local" keeps meaning the same thing on
both sides of a transition.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest

from database import async_session
from models.settings import SystemSetting
from services import offhours

NY = ZoneInfo("America/New_York")


def _cfg(start="01:00", end="07:00", tz="America/New_York", enabled=True):
    return offhours.OffHoursConfig(
        enabled=enabled,
        start=offhours.parse_hhmm(start),
        end=offhours.parse_hhmm(end),
        tz=ZoneInfo(tz),
    )


def _utc(y, m, d, hh, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# parse_hhmm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("01:00", datetime.time(1, 0)),
    ("00:00", datetime.time(0, 0)),
    ("23:59", datetime.time(23, 59)),
    ("7:05", datetime.time(7, 5)),
])
def test_parse_hhmm_accepts_valid_times(raw, expected):
    assert offhours.parse_hhmm(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "24:00", "01:60", "1pm", "0100", "01:00:00", None])
def test_parse_hhmm_rejects_junk(raw):
    with pytest.raises(ValueError):
        offhours.parse_hhmm(raw)


# ---------------------------------------------------------------------------
# in_window
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (0, False),   # before open
    (1, True),    # inclusive at open
    (4, True),
    (6, True),
    (7, False),   # exclusive at close
    (14, False),
])
def test_in_window_same_day_range(hour, expected):
    t = datetime.time(hour, 0)
    assert offhours.in_window(t, datetime.time(1, 0), datetime.time(7, 0)) is expected


@pytest.mark.parametrize("hour,expected", [
    (21, False),
    (22, True),   # inclusive at open
    (23, True),
    (0, True),    # across midnight
    (5, True),
    (6, False),   # exclusive at close
    (12, False),
])
def test_in_window_crossing_midnight(hour, expected):
    t = datetime.time(hour, 0)
    assert offhours.in_window(t, datetime.time(22, 0), datetime.time(6, 0)) is expected


def test_in_window_rejects_equal_start_and_end():
    """Equal bounds are ambiguous (always? never?) — refuse rather than guess."""
    with pytest.raises(ValueError):
        offhours.in_window(datetime.time(3, 0), datetime.time(1, 0), datetime.time(1, 0))


# ---------------------------------------------------------------------------
# dispatch_allowed
# ---------------------------------------------------------------------------

def test_dispatch_allowed_when_disabled_regardless_of_clock():
    cfg = _cfg(enabled=False)
    ok, reason = offhours.dispatch_allowed(cfg, _utc(2026, 6, 1, 18))  # 14:00 EDT
    assert ok is True
    assert reason == ""


def test_dispatch_blocked_outside_window_with_explanatory_message():
    cfg = _cfg()
    ok, reason = offhours.dispatch_allowed(cfg, _utc(2026, 6, 1, 18))  # 14:00 EDT
    assert ok is False
    assert "01:00" in reason
    assert "off-hours" in reason.lower()


def test_dispatch_allowed_inside_window():
    cfg = _cfg()
    ok, reason = offhours.dispatch_allowed(cfg, _utc(2026, 6, 1, 6))  # 02:00 EDT
    assert ok is True
    assert reason == ""


# ---------------------------------------------------------------------------
# DST — the reason we store an IANA zone rather than an offset
# ---------------------------------------------------------------------------

def test_window_holds_across_spring_forward():
    """2026-03-08: EST (UTC-5) until 07:00Z, EDT (UTC-4) after."""
    cfg = _cfg()
    assert offhours.dispatch_allowed(cfg, _utc(2026, 3, 8, 6, 30))[0] is True    # 01:30 EST
    assert offhours.dispatch_allowed(cfg, _utc(2026, 3, 8, 8, 30))[0] is True    # 04:30 EDT
    assert offhours.dispatch_allowed(cfg, _utc(2026, 3, 8, 12, 30))[0] is False  # 08:30 EDT


def test_window_holds_across_fall_back():
    """2026-11-01: 01:30 local happens twice; both are inside a 01:00–07:00 window."""
    cfg = _cfg()
    assert offhours.dispatch_allowed(cfg, _utc(2026, 11, 1, 5, 30))[0] is True   # 01:30 EDT
    assert offhours.dispatch_allowed(cfg, _utc(2026, 11, 1, 6, 30))[0] is True   # 01:30 EST
    assert offhours.dispatch_allowed(cfg, _utc(2026, 11, 1, 13, 30))[0] is False  # 08:30 EST


# ---------------------------------------------------------------------------
# next_open / next_close / seconds_until_open
# ---------------------------------------------------------------------------

def test_next_open_rolls_to_tomorrow_when_today_is_past():
    cfg = _cfg()
    nxt = offhours.next_open(cfg, _utc(2026, 6, 1, 18))  # 14:00 EDT on the 1st
    assert nxt.astimezone(NY) == datetime.datetime(2026, 6, 2, 1, 0, tzinfo=NY)


def test_next_open_is_later_today_when_still_ahead():
    cfg = _cfg()
    nxt = offhours.next_open(cfg, _utc(2026, 6, 1, 4))  # 00:00 EDT on the 1st
    assert nxt.astimezone(NY) == datetime.datetime(2026, 6, 1, 1, 0, tzinfo=NY)


def test_next_close_inside_window_is_the_current_windows_end():
    cfg = _cfg()
    nxt = offhours.next_close(cfg, _utc(2026, 6, 1, 6))  # 02:00 EDT
    assert nxt.astimezone(NY) == datetime.datetime(2026, 6, 1, 7, 0, tzinfo=NY)


def test_seconds_until_open_is_zero_while_open():
    assert offhours.seconds_until_open(_cfg(), _utc(2026, 6, 1, 6)) == 0


def test_seconds_until_open_is_zero_when_disabled():
    assert offhours.seconds_until_open(_cfg(enabled=False), _utc(2026, 6, 1, 18)) == 0


def test_seconds_until_open_counts_down_to_the_next_open():
    # 14:00 EDT -> 01:00 EDT next day = 11 hours
    assert offhours.seconds_until_open(_cfg(), _utc(2026, 6, 1, 18)) == 11 * 3600


# ---------------------------------------------------------------------------
# load_config — DB-backed
# ---------------------------------------------------------------------------

async def _seed(db, **kv):
    for k, v in kv.items():
        db.add(SystemSetting(key=k, value=v))
    await db.commit()


async def test_load_config_defaults_to_disabled(db):
    cfg = await offhours.load_config()
    assert cfg.enabled is False


async def test_load_config_reads_stored_values(db):
    await _seed(
        db,
        transcription_offhours_enabled="true",
        transcription_offhours_start="22:00",
        transcription_offhours_end="06:00",
        transcription_offhours_timezone="America/New_York",
    )
    cfg = await offhours.load_config()
    assert cfg.enabled is True
    assert cfg.start == datetime.time(22, 0)
    assert cfg.end == datetime.time(6, 0)
    assert cfg.tz == NY


async def test_load_config_falls_back_to_disabled_on_corrupt_values(db):
    """A bad row must never wedge the queue loop — degrade to 'no window'."""
    await _seed(
        db,
        transcription_offhours_enabled="true",
        transcription_offhours_start="nonsense",
        transcription_offhours_end="06:00",
    )
    cfg = await offhours.load_config()
    assert cfg.enabled is False


async def test_load_config_falls_back_on_unknown_timezone(db):
    await _seed(
        db,
        transcription_offhours_enabled="true",
        transcription_offhours_timezone="Mars/Olympus_Mons",
    )
    cfg = await offhours.load_config()
    assert cfg.enabled is False


# ---------------------------------------------------------------------------
# validate_settings — used by PUT /api/settings
# ---------------------------------------------------------------------------

def test_validate_settings_accepts_a_good_window():
    offhours.validate_settings({
        "transcription_offhours_start": "01:00",
        "transcription_offhours_end": "07:00",
        "transcription_offhours_timezone": "America/New_York",
    })  # should not raise


@pytest.mark.parametrize("patch", [
    {"transcription_offhours_start": "25:00"},
    {"transcription_offhours_end": "abc"},
    {"transcription_offhours_timezone": "Mars/Olympus_Mons"},
    {"transcription_offhours_start": "03:00", "transcription_offhours_end": "03:00"},
])
def test_validate_settings_rejects_bad_input(patch):
    payload = {
        "transcription_offhours_start": "01:00",
        "transcription_offhours_end": "07:00",
        "transcription_offhours_timezone": "UTC",
    }
    payload.update(patch)
    with pytest.raises(ValueError):
        offhours.validate_settings(payload)


def test_validate_settings_catches_equal_bounds_against_stored_values():
    """A PUT that only changes `start` must still be checked against the
    persisted `end` — otherwise a partial update can create start == end."""
    with pytest.raises(ValueError):
        offhours.validate_settings(
            {"transcription_offhours_start": "07:00"},
            stored={"transcription_offhours_end": "07:00"},
        )
