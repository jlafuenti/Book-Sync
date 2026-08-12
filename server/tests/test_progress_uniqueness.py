"""
Duplicate position rows must be impossible, and a read must never create one
(issue #64).

`GET /api/sync/progress/{type}/{id}` used to be get-or-**create**: it INSERTed on
a miss, and `user_progress` had no unique constraint. Two concurrent first reads
for the same book — a phone and a browser opening it at the same moment — each
inserted a row, and from then on every request for that media hit
`scalar_one_or_none()` on two rows and raised. That one book 500ed forever until
a row was deleted by hand.

Two defences, both pinned here:

* the GET is read-only and answers 204 when there is nothing to report;
* partial unique indexes on `user_progress` make the duplicate unrepresentable,
  and the write path recovers from the resulting `IntegrityError` by re-reading
  the row the racing transaction won instead of 500ing.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import services.position_service as position_service
from models.book import EBook
from models.progress import ProgressType, UserProgress
from models.bookmark import Bookmark
from tests.factories import make_book_pair, suspend_user_progress_uniqueness


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def _one_shot_none(monkeypatch, name):
    """Make the next call to `position_service.<name>` report "nothing there".

    A deterministic stand-in for the racing transaction: the real race is two
    sessions whose SELECTs both miss before either INSERT lands, which is not
    reproducible against a single SQLite connection. Forcing one miss after the
    row exists puts the write path in exactly the state that race leaves it in.
    """
    real = getattr(position_service, name)
    calls = {"n": 0}

    async def _fake(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real(*args, **kwargs)

    monkeypatch.setattr(position_service, name, _fake)


# ---------------------------------------------------------------- read-only GET


async def test_get_progress_for_unread_media_returns_204_and_creates_nothing(
    client, make_user, auth_header, db
):
    """The regression: a read must not manufacture a position."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    resp = await client.get(
        f"/api/sync/progress/ebook/{pair.ebook_id}", headers=auth_header(user)
    )

    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert await _count(db, UserProgress) == 0


async def test_repeated_first_gets_create_nothing_and_a_put_then_creates_one(
    client, make_user, auth_header, db
):
    """Two clients opening the same book leave no rows; the first write makes one."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    url = f"/api/sync/progress/ebook/{pair.ebook_id}"
    headers = auth_header(user)

    first = await client.get(url, headers=headers)
    second = await client.get(url, headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204
    assert await _count(db, UserProgress) == 0

    written = await client.put(
        f"/api/sync/position/pair/{pair.id}", headers=headers,
        json={"source": "ebook", "epub_chapter": 4, "epub_progress_percent": 40.0},
    )
    assert written.status_code == 200, written.text

    # The pair-scoped write projects onto both halves of the pair — one row each,
    # never two for the same media.
    rows = (await db.execute(
        select(UserProgress).where(UserProgress.ebook_id == pair.ebook_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].epub_chapter == 4

    # ...and the GET now reports it.
    got = await client.get(url, headers=headers)
    assert got.status_code == 200
    assert got.json()["epub_chapter"] == 4


async def test_get_progress_tolerates_pre_existing_duplicate_rows(
    client, make_user, auth_header, db
):
    """A database that hit the race before the constraint landed must still read.

    Rows are inserted with the constraint suspended, which is the state such a
    database is in until migration 0006 dedupes it.
    """
    from datetime import datetime

    pair = await make_book_pair(db)
    user = await make_user(username="reader")

    await suspend_user_progress_uniqueness(db)
    db.add_all([
        UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                     ebook_id=pair.ebook_id, epub_chapter=1,
                     is_completed=False, updated_at=datetime(2026, 1, 1)),
        UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                     ebook_id=pair.ebook_id, epub_chapter=9,
                     is_completed=False, updated_at=datetime(2026, 6, 1)),
    ])
    await db.commit()

    resp = await client.get(
        f"/api/sync/progress/ebook/{pair.ebook_id}", headers=auth_header(user)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["epub_chapter"] == 9  # the newest, not a 500


# ------------------------------------------------------------- the constraint


async def test_duplicate_user_progress_rows_are_rejected(db, make_user):
    """The schema, not the code, is what makes the duplicate impossible."""
    user = await make_user(username="reader")
    eb = EBook(title="E", filename="e.epub", file_path="/x/e.epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)

    db.add(UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                        ebook_id=eb.id, is_completed=False))
    await db.commit()

    db.add(UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                        ebook_id=eb.id, is_completed=False))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ------------------------------------------------------- first-write races


async def test_racing_first_position_writes_converge_on_one_row(
    client, make_user, auth_header, db, monkeypatch
):
    """A first write that misses the row another transaction just inserted must
    apply on top of it, not 500 on the unique index."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    headers = auth_header(user)

    seeded = await client.put(f"/api/sync/position/pair/{pair.id}", headers=headers, json={
        "source": "ebook", "epub_chapter": 1, "epub_sentence_index": 2,
    })
    assert seeded.status_code == 200, seeded.text

    _one_shot_none(monkeypatch, "read_position")

    raced = await client.put(f"/api/sync/position/pair/{pair.id}", headers=headers, json={
        "source": "ebook", "epub_chapter": 7, "epub_sentence_index": 3,
    })
    assert raced.status_code == 200, raced.text
    assert raced.json()["epub_chapter"] == 7

    assert await _count(db, Bookmark) == 1


async def test_racing_first_progress_projections_converge_on_one_row(
    client, make_user, auth_header, db, monkeypatch
):
    """Same race, one layer down: the `user_progress` projection insert."""
    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    headers = auth_header(user)

    seeded = await client.put(f"/api/sync/position/pair/{pair.id}", headers=headers, json={
        "source": "ebook", "epub_chapter": 1, "epub_sentence_index": 2,
    })
    assert seeded.status_code == 200, seeded.text

    _one_shot_none(monkeypatch, "latest_progress_row")

    raced = await client.put(f"/api/sync/position/pair/{pair.id}", headers=headers, json={
        "source": "ebook", "epub_chapter": 7, "epub_sentence_index": 3,
    })
    assert raced.status_code == 200, raced.text

    rows = (await db.execute(
        select(UserProgress).where(UserProgress.ebook_id == pair.ebook_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].epub_chapter == 7


async def test_a_stale_write_that_loses_the_race_is_still_rejected(
    client, make_user, auth_header, db, monkeypatch
):
    """Losing the insert race must not smuggle a stale write past the #54 check.

    The row we end up holding is the winner's, and it has never been compared
    against this request's `captured_at` — the check at the top of
    `apply_position` ran when there was no row to compare with.
    """
    from datetime import datetime, timedelta

    pair = await make_book_pair(db)
    user = await make_user(username="reader")
    headers = auth_header(user)
    newer = datetime(2026, 3, 1, 12, 0, 0)
    older = newer - timedelta(hours=1)

    seeded = await client.put(f"/api/sync/position/pair/{pair.id}", headers=headers, json={
        "source": "ebook", "epub_chapter": 5, "epub_sentence_index": 1,
        "captured_at": newer.isoformat(),
    })
    assert seeded.status_code == 200, seeded.text

    _one_shot_none(monkeypatch, "read_position")

    raced = await client.put(f"/api/sync/position/pair/{pair.id}", headers=headers, json={
        "source": "ebook", "epub_chapter": 0, "epub_sentence_index": 0,
        "captured_at": older.isoformat(),
    })
    assert raced.status_code == 409, raced.text
    assert raced.json()["epub_chapter"] == 5

    assert await _count(db, Bookmark) == 1
    stored = (await db.execute(select(Bookmark))).scalar_one()
    assert stored.epub_chapter == 5


async def test_an_unrelated_integrity_error_is_not_swallowed(db, make_user):
    """The savepoint recovery is for the uniqueness race, nothing else.

    A conflict the re-read cannot explain must surface, not be silently turned
    into "someone else won" — that would hide a genuine schema violation.
    """
    user = await make_user(username="reader")
    eb = EBook(title="E", filename="e.epub", file_path="/x/e.epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)

    db.add(UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                        ebook_id=eb.id, is_completed=False))
    await db.commit()

    async def _finds_nothing():
        return None

    with pytest.raises(IntegrityError):
        await position_service._insert_or_reread(
            db,
            UserProgress(user_id=user.id, media_type=ProgressType.EBOOK,
                         ebook_id=eb.id, is_completed=False),
            _finds_nothing,
        )
    await db.rollback()
