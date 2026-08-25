"""
The timestamp contract: naive UTC, everywhere, from one helper (issue #52).

`datetime.utcnow()` is deprecated on Python 3.12+ and returns a naive datetime
that reads like local time. The obvious fix — `datetime.now(timezone.utc)` — is
NOT safe here: every timestamp column in this schema is a bare `DateTime`
(there is no `timezone=True` anywhere), so SQLAlchemy reads them back naive.
Writing aware values while reading naive ones breaks the two places that
*compare* timestamps — `position_service.is_stale` and
`import_scheduler._due_sources` — with `TypeError: can't compare offset-naive
and offset-aware datetimes`.

So the contract is unchanged and explicit: `utils.utcnow()` produces naive UTC,
`schemas._naive_utc` normalizes inbound aware values to the same, and nothing
in the app hands an aware datetime to a column. These tests pin all three.
"""

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import utcnow

_SERVER_DIR = Path(__file__).resolve().parent.parent

# Modules that legitimately work in aware datetimes: `offhours` reasons about
# wall-clock time in a user-configured zone and never writes a DB column.
_AWARE_BY_DESIGN = {"services/offhours.py"}


def test_utcnow_is_naive_and_actually_utc():
    now = utcnow()
    assert now.tzinfo is None, "an aware value here would break is_stale on read-back"
    reference = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(now - reference) < timedelta(seconds=5)


def test_utcnow_is_not_local_time():
    """The trap `utcnow()` set: a naive value that looks like local time. This
    only proves anything on a machine that isn't already on UTC, so it skips
    rather than passing vacuously."""
    local_offset = datetime.now().astimezone().utcoffset()
    if local_offset == timedelta(0):
        pytest.skip("machine is on UTC; nothing to distinguish")
    assert abs(utcnow() - datetime.now()) > timedelta(minutes=30)


def _python_sources(include_tests: bool = False):
    """Every first-party module. `alembic/` is excluded because its revisions
    are historical records of what already ran, not live code."""
    for path in sorted(_SERVER_DIR.rglob("*.py")):
        rel = path.relative_to(_SERVER_DIR).as_posix()
        if rel.startswith(("alembic/", ".venv/", "venv/")):
            continue
        if rel.startswith("tests/") and not include_tests:
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_no_source_file_still_calls_datetime_utcnow():
    """The ratchet, and the only gate on this. (A runtime warning filter can't
    tell our frames from a dependency's — the since-removed python-jose used
    to call utcnow() internally, which is why pytest.ini once ignored it.)

    Static beats runtime here anyway: this covers files no test executes.
    Tests are included too, so a fixture can't quietly reintroduce the call.

    Walks the AST rather than grepping, so prose in a docstring (`utils.utcnow`
    explains what it replaces) isn't mistaken for a call, and the bare
    `utils.utcnow` this codebase does use isn't either.
    """
    offenders = []
    for rel, text in _python_sources(include_tests=True):
        for node in ast.walk(ast.parse(text)):
            # Matches both import styles: `datetime.utcnow` and
            # `datetime.datetime.utcnow`. A bare `utcnow` (ours) is a Name.
            if isinstance(node, ast.Attribute) and node.attr == "utcnow":
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "datetime.utcnow() is deprecated — use utils.utcnow() instead: "
        + ", ".join(sorted(offenders))
    )


def test_no_source_file_writes_an_aware_now_outside_the_aware_modules():
    """`datetime.now(timezone.utc)` belongs only in `utils.utcnow` (which strips
    the tzinfo again) and in modules that never touch a naive DB column."""
    pattern = re.compile(r"\.now\(\s*(datetime\.)?(timezone\.utc|UTC|tz=)")
    offenders = [
        f"{rel}:{i}"
        for rel, text in _python_sources()
        if rel not in _AWARE_BY_DESIGN and rel != "utils.py"
        for i, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        "an aware datetime written to a naive DateTime column reads back naive "
        "and breaks timestamp comparisons — use utils.utcnow(): "
        + ", ".join(offenders)
    )


async def test_the_staleness_comparison_still_works_end_to_end(
    client, make_user, auth_header, db
):
    """The behaviour the naive contract exists to protect: an inbound aware
    `captured_at` (JS `toISOString()` / Kotlin `Instant.toString()`) must be
    comparable against the naive value read back from the column."""
    from tests.factories import make_book_pair

    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    def _put(chapter, captured_at):
        return client.put(
            f"/api/sync/position/pair/{pair.id}", headers=auth_header(user),
            json={"source": "ebook", "epub_chapter": chapter,
                  "captured_at": captured_at},
        )

    assert (await _put(5, "2026-07-20T12:00:00Z")).status_code == 200
    stale = await _put(0, "2026-07-20T11:00:00Z")
    assert stale.status_code == 409
    assert stale.json()["epub_chapter"] == 5


async def test_a_server_stamped_captured_at_is_comparable_on_the_next_write(
    client, make_user, auth_header, db
):
    """A write that omits `captured_at` gets stamped with `utils.utcnow()`. That
    stamped value is stored and then compared against on the *next* write — an
    aware stamp would raise TypeError there rather than at the write itself."""
    from tests.factories import make_book_pair

    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    seeded = await client.put(
        f"/api/sync/position/pair/{pair.id}", headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 5},
    )
    assert seeded.status_code == 200, seeded.text

    # Older than "now", so it must be judged stale rather than crashing.
    stale = await client.put(
        f"/api/sync/position/pair/{pair.id}", headers=auth_header(user),
        json={"source": "ebook", "epub_chapter": 0,
              "captured_at": "2020-01-01T00:00:00Z"},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["epub_chapter"] == 5
