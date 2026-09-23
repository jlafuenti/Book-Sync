"""
A pair is strictly one-to-one (issue #691): an ebook may be in at most one
`BookPair`, and an audiobook may be in at most one.

Before this, `book_pairs` was only unique on the *combination* of the two
ids (`uq_book_pairs_pair`), and `create_pair` only rejected the exact same
combination twice. Pairing one ebook to two different audiobooks — once by
`auto_match` during a scan, once by hand three days later, in the field case
that opened the issue — succeeded both times: two ~34-hour transcription jobs
for one book, and a reader's position able to land on either pair since
`user_progress`/`bookmarks` key on `book_pair_id`.

This file covers the create endpoint's new 409s and the DB-level guarantee
(`ux_book_pairs_ebook_id` / `ux_book_pairs_audiobook_id`, migration
`0025_book_pairs_one_to_one`) that backs them. `auto_match`'s side is covered
in `test_auto_match.py` (it already excluded paired rows from its candidate
query — `test_already_paired_books_are_not_considered` — this just adds the
"two viable candidates in one run" case). The EPUB-conversion relink guard
(`routers.library.PairRelinkConflict`) is unit-tested directly below against
`_relink_or_cleanup_pairs`, and end-to-end below that against the single-item
and bulk `convert` endpoints — see `test_convert_realign.py` for the same
endpoints' non-conflict conversion/realign behavior, which this file does not
repeat.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models.book import AudioBook, BookPair, EBook, PairStatus
from tests.factories import make_audiobook, make_ebook


@pytest.fixture
async def library_client(make_client, make_user, auth_header):
    from routers import library

    user = await make_user(role="editor")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


async def _create(c, headers, ebook_id, audiobook_id):
    return await c.post(
        "/api/library/pairs",
        json={"ebook_id": ebook_id, "audiobook_id": audiobook_id},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /api/library/pairs
# ---------------------------------------------------------------------------

async def test_first_pair_for_a_book_still_returns_201(db, library_client):
    c, headers = library_client
    eb = await make_ebook(db)
    ab = await make_audiobook(db)

    resp = await _create(c, headers, eb.id, ab.id)

    assert resp.status_code == 201, resp.text


async def test_second_pair_for_the_same_ebook_returns_409_naming_the_pair(
    db, library_client
):
    c, headers = library_client
    eb = await make_ebook(db)
    ab1 = await make_audiobook(db, filename="a1.m4b")
    ab2 = await make_audiobook(db, filename="a2.m4b")

    first = await _create(c, headers, eb.id, ab1.id)
    assert first.status_code == 201, first.text
    existing_pair_id = first.json()["id"]

    resp = await _create(c, headers, eb.id, ab2.id)

    assert resp.status_code == 409, resp.text
    assert str(existing_pair_id) in resp.json()["detail"]

    rows = (await db.execute(
        select(BookPair).where(BookPair.ebook_id == eb.id)
    )).scalars().all()
    assert [r.id for r in rows] == [existing_pair_id], "a second row was created"


async def test_second_pair_for_the_same_audiobook_returns_409_naming_the_pair(
    db, library_client
):
    c, headers = library_client
    eb1 = await make_ebook(db, filename="e1.epub")
    eb2 = await make_ebook(db, filename="e2.epub")
    ab = await make_audiobook(db)

    first = await _create(c, headers, eb1.id, ab.id)
    assert first.status_code == 201, first.text
    existing_pair_id = first.json()["id"]

    resp = await _create(c, headers, eb2.id, ab.id)

    assert resp.status_code == 409, resp.text
    assert str(existing_pair_id) in resp.json()["detail"]

    rows = (await db.execute(
        select(BookPair).where(BookPair.audiobook_id == ab.id)
    )).scalars().all()
    assert [r.id for r in rows] == [existing_pair_id], "a second row was created"


async def test_the_exact_same_combination_twice_still_returns_409(
    db, library_client
):
    """The original guard (issue #256/#458): unchanged by the new one-to-one
    checks, which both catch this case too, but the exact-duplicate message
    is checked first and stays distinct."""
    c, headers = library_client
    eb = await make_ebook(db)
    ab = await make_audiobook(db)

    first = await _create(c, headers, eb.id, ab.id)
    assert first.status_code == 201, first.text

    resp = await _create(c, headers, eb.id, ab.id)

    assert resp.status_code == 409, resp.text
    assert (await db.execute(select(BookPair))).scalars().all().__len__() == 1


# ---------------------------------------------------------------------------
# DB level — the constraint is the backstop, not just the endpoint's guard.
# ---------------------------------------------------------------------------

async def test_a_second_row_with_the_same_ebook_id_raises_integrity_error(db):
    eb = await make_ebook(db)
    ab1 = await make_audiobook(db, filename="a1.m4b")
    ab2 = await make_audiobook(db, filename="a2.m4b")
    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab1.id, status=PairStatus.SYNCED))
    await db.commit()

    db.add(BookPair(ebook_id=eb.id, audiobook_id=ab2.id, status=PairStatus.SYNCED))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_a_second_row_with_the_same_audiobook_id_raises_integrity_error(db):
    eb1 = await make_ebook(db, filename="e1.epub")
    eb2 = await make_ebook(db, filename="e2.epub")
    ab = await make_audiobook(db)
    db.add(BookPair(ebook_id=eb1.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
    await db.commit()

    db.add(BookPair(ebook_id=eb2.id, audiobook_id=ab.id, status=PairStatus.SYNCED))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ---------------------------------------------------------------------------
# EPUB-conversion relink guard (routers.library._relink_or_cleanup_pairs)
# ---------------------------------------------------------------------------

async def test_relink_conflict_when_the_epub_target_already_has_a_pair(db):
    """A MOBI source paired to audiobook A, and its already-registered EPUB
    sibling separately paired to audiobook B: converting the source and
    asking to re-point its pair onto the EPUB would try to give the EPUB a
    second pair, which the one-to-one rule forbids. `_relink_or_cleanup_pairs`
    must refuse (not silently drop either pair) and touch nothing."""
    from routers.library import PairRelinkConflict, _relink_or_cleanup_pairs

    source = await make_ebook(db, filename="book.mobi", format="mobi")
    epub_sibling = await make_ebook(db, filename="book.epub")
    audio_a = await make_audiobook(db, filename="a.m4b")
    audio_b = await make_audiobook(db, filename="b.m4b")
    source_pair = BookPair(ebook_id=source.id, audiobook_id=audio_a.id,
                           status=PairStatus.SYNCED)
    epub_pair = BookPair(ebook_id=epub_sibling.id, audiobook_id=audio_b.id,
                         status=PairStatus.SYNCED)
    db.add_all([source_pair, epub_pair])
    await db.commit()
    await db.refresh(source_pair)
    await db.refresh(epub_pair)

    # Captured before the rollback below expires these ORM objects — an
    # expired attribute re-fetches lazily, which an async session cannot do
    # outside an `await`.
    source_id, epub_id = source.id, epub_sibling.id
    audio_a_id, audio_b_id = audio_a.id, audio_b.id
    epub_pair_id = epub_pair.id

    with pytest.raises(PairRelinkConflict) as exc_info:
        await _relink_or_cleanup_pairs(source_id, epub_sibling, db)
    assert str(epub_pair_id) in str(exc_info.value)

    await db.rollback()
    rows = (await db.execute(select(BookPair))).scalars().all()
    assert {(r.ebook_id, r.audiobook_id) for r in rows} == {
        (source_id, audio_a_id), (epub_id, audio_b_id),
    }, "the conflict guard must not have touched either pair"


async def test_relink_still_works_when_the_epub_target_has_no_pair(db):
    """The common case — converting a source that is the only pair on its
    ebook, onto an EPUB sibling nobody has paired yet — is unaffected."""
    from routers.library import _relink_or_cleanup_pairs

    source = await make_ebook(db, filename="book.mobi", format="mobi")
    epub_sibling = await make_ebook(db, filename="book.epub")
    audio = await make_audiobook(db)
    source_pair = BookPair(ebook_id=source.id, audiobook_id=audio.id,
                           status=PairStatus.SYNCED)
    db.add(source_pair)
    await db.commit()
    await db.refresh(source_pair)

    relinked = await _relink_or_cleanup_pairs(source.id, epub_sibling, db)
    await db.commit()

    assert relinked == [source_pair.id]
    await db.refresh(source_pair)
    assert source_pair.ebook_id == epub_sibling.id


# ---------------------------------------------------------------------------
# End-to-end: the conflict surfaces through the convert endpoints
# (routers.library.convert_unsupported_file / convert_all_unsupported), not
# just from the unit-level `_relink_or_cleanup_pairs` call above.
# ---------------------------------------------------------------------------

async def _seed_mobi_with_a_paired_epub_sibling(db, tmp_path):
    """A MOBI paired to audio_a, whose already-registered .epub sibling (same
    stem, same directory — what Calibre's own conversion would produce) is
    separately paired to audio_b. Converting the MOBI and asking to delete
    the source would try to re-point its pair onto the EPUB, which already
    has one. Returns (source_ebook_id, epub_path, epub_pair_id)."""
    from models.book import AudioBook, EBook

    mobi_path = tmp_path / "book.mobi"
    mobi_path.write_bytes(b"fake mobi bytes")
    epub_path = tmp_path / "book.epub"

    source = EBook(title="Axis Test", filename="book.mobi",
                   file_path=str(mobi_path), format="mobi")
    epub_sibling = EBook(title="Axis Test", filename="book.epub",
                         file_path=str(epub_path), format="epub")
    audio_a = AudioBook(title="A", filename="a.m4b", file_path=str(tmp_path / "a.m4b"))
    audio_b = AudioBook(title="B", filename="b.m4b", file_path=str(tmp_path / "b.m4b"))
    db.add_all([source, epub_sibling, audio_a, audio_b])
    await db.flush()
    source_pair = BookPair(ebook_id=source.id, audiobook_id=audio_a.id,
                           status=PairStatus.SYNCED)
    epub_pair = BookPair(ebook_id=epub_sibling.id, audiobook_id=audio_b.id,
                         status=PairStatus.SYNCED)
    db.add_all([source_pair, epub_pair])
    await db.commit()
    await db.refresh(epub_pair)
    return source.id, epub_path, epub_pair.id


def _fake_calibre_writes(epub_path, monkeypatch):
    def _convert(_src_path):
        epub_path.write_bytes(b"fake epub bytes")
        return str(epub_path)
    monkeypatch.setattr("routers.library._convert_to_epub_sync", _convert)


async def test_convert_single_returns_409_when_the_epub_target_already_has_a_pair(
    db, tmp_path, monkeypatch, make_client, make_user, auth_header,
):
    from routers import library

    source_id, epub_path, epub_pair_id = await _seed_mobi_with_a_paired_epub_sibling(
        db, tmp_path
    )
    _fake_calibre_writes(epub_path, monkeypatch)
    user = await make_user(role="admin")

    async with make_client(library.router) as c:
        resp = await c.post(
            f"/api/library/unsupported/{source_id}/convert?delete_source=true",
            headers=auth_header(user),
        )

    assert resp.status_code == 409, resp.text
    assert str(epub_pair_id) in resp.json()["detail"]

    # Nothing was touched: the source file and both pairs survive untouched.
    assert (tmp_path / "book.mobi").exists()
    rows = (await db.execute(select(BookPair))).scalars().all()
    assert len(rows) == 2


async def test_convert_all_reports_the_conflict_without_aborting_the_batch(
    db, tmp_path, monkeypatch, make_client, make_user, auth_header,
):
    """A relink conflict on one MOBI must not stop a second, unrelated MOBI
    in the same `/unsupported/convert-all` call from converting normally."""
    from models.book import EBook
    from routers import library

    source_id, epub_path, epub_pair_id = await _seed_mobi_with_a_paired_epub_sibling(
        db, tmp_path
    )
    other_mobi = tmp_path / "other.mobi"
    other_mobi.write_bytes(b"fake mobi bytes")
    other = EBook(title="Other Book", filename="other.mobi",
                  file_path=str(other_mobi), format="mobi")
    db.add(other)
    await db.commit()

    def _convert(src_path):
        # Mirrors what Calibre actually produces: <stem>.epub next to the
        # source — the exact path `_seed_mobi_with_a_paired_epub_sibling`
        # pre-registered `epub_sibling` under, so the source's own conversion
        # resolves to the already-paired row while `other.mobi`'s resolves to
        # a brand new one.
        out = Path(src_path).with_suffix(".epub")
        out.write_bytes(b"fake epub bytes")
        return str(out)
    monkeypatch.setattr("routers.library._convert_to_epub_sync", _convert)
    user = await make_user(role="admin")

    async with make_client(library.router) as c:
        resp = await c.post(
            "/api/library/unsupported/convert-all?delete_source=true",
            headers=auth_header(user),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["relink_conflicts"]) == 1
    assert str(epub_pair_id) in body["relink_conflicts"][0]["error"]
    assert "other.mobi" in body["succeeded"]

    # The conflicting source ebook was left alone; the unrelated one converted.
    assert (tmp_path / "book.mobi").exists()
    assert not other_mobi.exists(), "the unrelated conversion should have deleted its source"
    rows = (await db.execute(select(BookPair))).scalars().all()
    assert len(rows) == 2
