"""
Metadata write-back keeps the sync-map audit hash in step (issue #533).

`write_ebook_metadata` (`services/tag_writer.py`) rewrites the EPUB's OPF in
place; the archive's bytes change even though the text a sync map was aligned
against does not, so the composite whole-file hash used for provenance
(`sync_maps.epub_file_hash`, `services/sync_map_audit.py`) moves out from under
a healthy map. An ordinary title edit on a paired ebook used to turn a healthy
pair into a `stale` one.

Fix (option 2 from the issue): the write-back paths that knowingly rewrite a
file the server owns -- `PATCH /api/library/ebooks/{id}`,
`POST /api/library/pairs/{id}/resolve-discrepancies`, and the audiobook
equivalents -- recompute the file's hash right after a successful write and
store it back: on the book row's own `file_hash`/`file_size`, and on the sync
map's `epub_file_hash` when the ebook belongs to a pair that has one recorded.
A file replaced from outside the server never goes through this path, so the
audit still catches that case (the negative test below).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from models.book import AudioBook, BookPair, EBook, PairStatus
from routers import library
from services import sync_map_audit
from services.file_hash import hash_file
from tests.factories import make_sync_map, write_epub

CH1 = (
    "<html><body><p>The harbour lay still under a flat grey sky. "
    "Althea counted the ships at anchor and found one missing.</p></body></html>"
)

PREVIEWS = [
    "The harbour lay still under a flat grey sky.",
    "Althea counted the ships at anchor and found one missing.",
]


def _points(previews):
    return [(0, i, i * 5000, text) for i, text in enumerate(previews)]


async def _seed_pair(db, tmp_path, *, filename="book.epub"):
    """An EBook backed by a real EPUB on disk, paired with an audiobook, with a
    sync map whose recorded hash matches the file as written -- the "healthy,
    aligned" starting state a metadata edit must not disturb."""
    path = write_epub(tmp_path / filename, [("c1.xhtml", CH1)])
    eb = EBook(title="Axis Test", filename=filename, file_path=path,
               file_hash=hash_file(path))
    audio_path = tmp_path / f"{filename.rsplit('.', 1)[0]}.m4b"
    audio_path.write_bytes(b"placeholder audio bytes")
    ab = AudioBook(title="Axis Test", filename=audio_path.name,
                   file_path=str(audio_path), file_hash=hash_file(str(audio_path)))
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    await make_sync_map(db, pair.id, _points(PREVIEWS), epub_file_hash=hash_file(path))
    return pair, path


@pytest.fixture
async def editor_headers(make_user, auth_header):
    user = await make_user(username="wb-editor", role="editor")
    return auth_header(user)


async def test_patching_ebook_metadata_keeps_the_audit_healthy(
    db, tmp_path, make_client, editor_headers,
):
    pair, path = await _seed_pair(db, tmp_path)

    async with make_client(library.router) as c:
        resp = await c.patch(
            f"/api/library/ebooks/{pair.ebook_id}",
            headers=editor_headers,
            json={"title": "A New Title"},
        )
    assert resp.status_code == 200

    [row] = await sync_map_audit.audit_sync_maps(db)
    assert row["hash_status"] == "match"
    assert row["status"] == "healthy"

    reread = await db.get(EBook, pair.ebook_id)
    await db.refresh(reread)
    assert reread.file_hash == hash_file(path)
    assert reread.file_size == len(open(path, "rb").read())


async def test_resolving_discrepancies_keeps_the_audit_healthy_on_the_ebook_side(
    db, tmp_path, make_client, editor_headers,
):
    pair, path = await _seed_pair(db, tmp_path)

    async with make_client(library.router) as c:
        resp = await c.post(
            f"/api/library/pairs/{pair.id}/resolve-discrepancies",
            headers=editor_headers,
            json={"ebook_updates": {"title": "A Different Title"},
                  "audiobook_updates": {}},
        )
    assert resp.status_code == 200

    [row] = await sync_map_audit.audit_sync_maps(db)
    assert row["hash_status"] == "match"
    assert row["status"] == "healthy"


async def test_a_file_replaced_outside_the_server_still_reports_stale(db, tmp_path):
    """The negative case: nothing the server didn't itself just rewrite should
    have its hash silently refreshed. A file swapped out from under the
    library by some other tool must still be caught."""
    pair, path = await _seed_pair(db, tmp_path)

    write_epub(
        path,
        [("c1.xhtml", "<html><body><p>Entirely different text, "
                       "nothing like the original.</p></body></html>")],
    )

    [row] = await sync_map_audit.audit_sync_maps(db)
    assert row["hash_status"] == "mismatch"
    assert row["status"] == "stale"


async def test_patching_audiobook_metadata_refreshes_its_stored_hash(
    db, tmp_path, make_client, editor_headers, monkeypatch,
):
    """No sync-map column tracks an audio hash today (`epub_file_hash` is the
    only provenance field `SyncMap` has), so there is nothing for the drift
    audit to keep in step on the audio side. The audiobook's own `file_hash`
    still needs refreshing after a write-back, the same as the ebook's --
    otherwise it silently stops describing the file on disk (issue #533,
    "the book row's own stored hash/size ... since a rehash would otherwise
    flag it too")."""
    pair, _path = await _seed_pair(db, tmp_path)
    ab = await db.get(AudioBook, pair.audiobook_id)
    old_hash = ab.file_hash

    def _fake_write(filepath, book):
        # Stand in for mutagen rewriting the container -- the real writer
        # can't tag the placeholder bytes `_seed_pair` wrote, but any
        # in-place rewrite is what this test needs to exercise.
        with open(filepath, "ab") as f:
            f.write(b"more bytes from a tag rewrite")

    monkeypatch.setattr(library, "_write_audiobook_metadata", _fake_write)

    async with make_client(library.router) as c:
        resp = await c.patch(
            f"/api/library/audiobooks/{pair.audiobook_id}",
            headers=editor_headers,
            json={"title": "New Audio Title"},
        )
    assert resp.status_code == 200

    reread = await db.get(AudioBook, pair.audiobook_id)
    await db.refresh(reread)
    new_hash = hash_file(reread.file_path)
    assert new_hash != old_hash
    assert reread.file_hash == new_hash


async def test_an_ebook_cannot_be_in_two_pairs(db, tmp_path):
    """An ebook carrying two aligned maps through two pairs used to be
    possible — the pair constraint was on (ebook, audiobook), not on either id
    alone — and this test used to prove a metadata edit kept both healthy.

    Issue #691 closed that: a pair is now strictly one-to-one, so this second
    pair is exactly the state that used to let the same book be queued for
    transcription twice. `ux_book_pairs_ebook_id` rejects it before a second
    aligned map (or a second write-back refresh) can exist to test.
    """
    pair, path = await _seed_pair(db, tmp_path)
    other_audio = tmp_path / "other.m4b"
    other_audio.write_bytes(b"other placeholder audio bytes")
    ab2 = AudioBook(title="Axis Test", filename=other_audio.name,
                    file_path=str(other_audio), file_hash=hash_file(str(other_audio)))
    db.add(ab2)
    await db.flush()
    db.add(BookPair(ebook_id=pair.ebook_id, audiobook_id=ab2.id, status=PairStatus.SYNCED))

    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
