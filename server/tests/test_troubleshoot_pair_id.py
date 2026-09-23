"""Issue #700 — file-level Troubleshoot items carry the file's `pair_id`.

Every file-level category (`missing`, `audio_corrupt`, `duplicate`, …) used to
be built with `_item_dict(item, type, detail)` and no `pair_id`, so it was
always `null`, even for a paired file. That read as "unpaired, low stakes" on
exactly the item that mattered most: a corrupt audiobook whose pair was queued
for transcription, about to spend hours of worker time producing a sync map
from mostly-missing audio.

Two things are pinned here: a paired file's item names its pair (and an
unpaired file's item still says `null`), and a file whose defect makes a
transcript useless says in `detail` when its pair has a job waiting or running,
so the operator can pull the job before it runs.
"""

import pytest
from sqlalchemy import select

from models.book import AudioBook, EBook
from models.library_issue import LibraryCheckResult
from models.transcription_queue import TranscriptionQueueItem
from routers import troubleshoot
from services import chapter_repair
from tests.factories import make_audiobook, make_book_pair, make_ebook


async def _issues(make_client, user, auth_header):
    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["categories"]


async def _sides(db, pair):
    eb = (await db.execute(select(EBook).where(EBook.id == pair.ebook_id))).scalar_one()
    ab = (await db.execute(select(AudioBook).where(AudioBook.id == pair.audiobook_id))).scalar_one()
    return eb, ab


def _item(rows, item_type, item_id):
    matches = [r for r in rows if r["item_type"] == item_type and r["item_id"] == item_id]
    assert len(matches) == 1, f"expected one {item_type} {item_id} in {rows}"
    return matches[0]


def _write(path, size):
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return str(path)


@pytest.fixture
async def editor(make_user):
    return await make_user(role="editor")


# ---------------------------------------------------------------------------
# pair_id on every file-level category
# ---------------------------------------------------------------------------


async def test_missing_files_carry_their_pair_id(db, make_client, editor, auth_header):
    """Factory paths point at nothing on disk, so both sides land in `missing`."""
    pair = await make_book_pair(db)
    loose = await make_ebook(db, title="Loose")
    eb, ab = await _sides(db, pair)

    rows = (await _issues(make_client, editor, auth_header))["missing"]

    assert _item(rows, "ebook", eb.id)["pair_id"] == pair.id
    assert _item(rows, "audiobook", ab.id)["pair_id"] == pair.id
    assert _item(rows, "ebook", loose.id)["pair_id"] is None


async def test_zero_byte_and_chapter_encoding_items_carry_their_pair_id(
    db, make_client, editor, auth_header, monkeypatch, tmp_path
):
    pair = await make_book_pair(db)
    eb, ab = await _sides(db, pair)
    eb.file_path = _write(tmp_path / "tiny.epub", 10)
    ab.file_path = _write(tmp_path / "tiny.m4b", 10)
    await db.commit()
    monkeypatch.setattr(chapter_repair, "check_chapter_encoding_cached",
                        lambda p: (False, "bad chapter title"))

    cats = await _issues(make_client, editor, auth_header)

    assert _item(cats["zero_byte"], "ebook", eb.id)["pair_id"] == pair.id
    assert _item(cats["zero_byte"], "audiobook", ab.id)["pair_id"] == pair.id
    assert _item(cats["chapter_encoding_bad"], "audiobook", ab.id)["pair_id"] == pair.id


async def test_unsupported_format_items_carry_their_pair_id(db, make_client, editor, auth_header):
    pair = await make_book_pair(db)
    eb, _ = await _sides(db, pair)
    eb.format = "mobi"
    await db.commit()

    rows = (await _issues(make_client, editor, auth_header))["unsupported_format"]

    assert _item(rows, "ebook", eb.id)["pair_id"] == pair.id


@pytest.mark.parametrize("check_type,item_type,detail,category", [
    ("audio_integrity", "audiobook", "decode failed", "audio_corrupt"),
    ("ebook_integrity", "ebook", "DRM protected", "ebook_drm"),
    ("ebook_integrity", "ebook", "bad zip", "ebook_unreadable"),
])
async def test_integrity_items_carry_their_pair_id(
    db, make_client, editor, auth_header, check_type, item_type, detail, category
):
    pair = await make_book_pair(db)
    eb, ab = await _sides(db, pair)
    item_id = ab.id if item_type == "audiobook" else eb.id
    db.add(LibraryCheckResult(item_type=item_type, item_id=item_id,
                              check_type=check_type, ok=False, detail=detail))
    await db.commit()

    rows = (await _issues(make_client, editor, auth_header))[category]

    assert _item(rows, item_type, item_id)["pair_id"] == pair.id


async def test_duplicate_items_carry_their_pair_id(db, make_client, editor, auth_header):
    pair = await make_book_pair(db)
    eb, _ = await _sides(db, pair)
    eb.file_hash = "a" * 64
    await db.commit()
    copy = await make_ebook(db, title="Copy", file_hash="a" * 64)

    rows = (await _issues(make_client, editor, auth_header))["duplicate"]

    assert _item(rows, "ebook", eb.id)["pair_id"] == pair.id
    assert _item(rows, "ebook", copy.id)["pair_id"] is None


async def test_possible_duplicate_items_carry_their_pair_id(db, make_client, editor, auth_header):
    """Exact ebook size plus a matching title is a qualifying candidate (#692)."""
    pair = await make_book_pair(db, ebook_title="Same Title")
    eb, _ = await _sides(db, pair)
    eb.file_size = 123_456
    await db.commit()
    copy = await make_ebook(db, title="Same Title", filename="copy.epub", file_size=123_456)

    rows = (await _issues(make_client, editor, auth_header))["possible_duplicate"]

    assert _item(rows, "ebook", eb.id)["pair_id"] == pair.id
    assert _item(rows, "ebook", copy.id)["pair_id"] is None


async def test_missing_cover_items_carry_their_pair_id(db, make_client, editor, auth_header):
    pair = await make_book_pair(db)
    eb, ab = await _sides(db, pair)
    loose = await make_audiobook(db, title="Loose")

    rows = (await _issues(make_client, editor, auth_header))["missing_cover"]

    assert _item(rows, "ebook", eb.id)["pair_id"] == pair.id
    assert _item(rows, "audiobook", ab.id)["pair_id"] == pair.id
    assert _item(rows, "audiobook", loose.id)["pair_id"] is None


# ---------------------------------------------------------------------------
# A defective file whose pair has a transcription job says so
# ---------------------------------------------------------------------------


async def _corrupt_audio(db, pair):
    _, ab = await _sides(db, pair)
    db.add(LibraryCheckResult(item_type="audiobook", item_id=ab.id,
                              check_type="audio_integrity", ok=False,
                              detail="Two thirds of the audio decodes to nothing"))
    await db.commit()
    return ab


@pytest.mark.parametrize("status,expected", [
    ("pending", "queued for transcription"),
    ("in_progress", "being transcribed now"),
])
async def test_corrupt_file_with_a_live_job_says_so(
    db, make_client, editor, auth_header, status, expected
):
    pair = await make_book_pair(db)
    ab = await _corrupt_audio(db, pair)
    db.add(TranscriptionQueueItem(book_pair_id=pair.id, status=status))
    await db.commit()

    item = _item((await _issues(make_client, editor, auth_header))["audio_corrupt"],
                 "audiobook", ab.id)

    assert item["detail"].startswith("Two thirds of the audio decodes to nothing")
    assert expected in item["detail"]


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", None])
async def test_corrupt_file_without_a_live_job_has_no_queue_note(
    db, make_client, editor, auth_header, status
):
    pair = await make_book_pair(db)
    ab = await _corrupt_audio(db, pair)
    if status:
        db.add(TranscriptionQueueItem(book_pair_id=pair.id, status=status))
        await db.commit()

    item = _item((await _issues(make_client, editor, auth_header))["audio_corrupt"],
                 "audiobook", ab.id)

    assert item["detail"] == "Two thirds of the audio decodes to nothing"


async def test_queue_note_applies_to_missing_files_too(db, make_client, editor, auth_header):
    """A missing file makes a transcript just as useless as a corrupt one."""
    pair = await make_book_pair(db)
    eb, ab = await _sides(db, pair)
    db.add(TranscriptionQueueItem(book_pair_id=pair.id, status="pending"))
    await db.commit()

    rows = (await _issues(make_client, editor, auth_header))["missing"]

    assert "queued for transcription" in _item(rows, "audiobook", ab.id)["detail"]
    assert "queued for transcription" in _item(rows, "ebook", eb.id)["detail"]


async def test_cosmetic_categories_get_no_queue_note(db, make_client, editor, auth_header):
    """A missing cover does not spoil a transcript; the note would be noise."""
    pair = await make_book_pair(db)
    _, ab = await _sides(db, pair)
    db.add(TranscriptionQueueItem(book_pair_id=pair.id, status="pending"))
    await db.commit()

    rows = (await _issues(make_client, editor, auth_header))["missing_cover"]

    assert _item(rows, "audiobook", ab.id)["detail"] == "No cover image"
