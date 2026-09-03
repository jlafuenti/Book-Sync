"""
Sync-map / EPUB drift audit (issue #295).

Pair 260's live map resolved audio positions to text that does not exist
anywhere in the EPUB now on disk: the map had been aligned against a different
parse or edition of the file. The reader degrades gracefully, but nothing could
*detect* the drift, so nothing could tell an operator which of ~135 pairs needed
re-aligning.

Two independent signals, both exercised here:

* **provenance** — `sync_maps.epub_file_hash`, stamped at save time. A stored
  hash that no longer matches the file on disk is proof the map describes a
  different document. NULL means the map predates the column: unknown, not
  healthy.
* **evidence** — a bounded sample of the map's stored sentence previews, looked
  up in the EPUB's whole-spine text. A poor hit rate is drift regardless of what
  the hash says, and it is the only signal available for a legacy map.
"""

import pytest

from models.book import EBook, AudioBook, BookPair, PairStatus
from models.transcript import AudioTranscript
from services import sync_map_audit
from services.file_hash import hash_file
from tests.factories import make_sync_map, write_epub

# Two spine documents of real prose. The previews a healthy map stores are
# verbatim slices of these sentences.
CH1 = (
    "<html><body><p>The harbour lay still under a flat grey sky. "
    "Althea counted the ships at anchor and found one missing. "
    "She would have to tell her father before nightfall.</p></body></html>"
)
CH2 = (
    "<html><body><p>Brashen kept his own counsel on the matter. "
    "The crew had seen worse crossings than this one. "
    "He set his shoulder to the capstan and heaved.</p></body></html>"
)

PRESENT_PREVIEWS = [
    "The harbour lay still under a flat grey sky.",
    "Althea counted the ships at anchor and found one missing.",
    "She would have to tell her father before nightfall.",
    "Brashen kept his own counsel on the matter.",
    "The crew had seen worse crossings than this one.",
    "He set his shoulder to the capstan and heaved.",
]

# The pair-260 signature: plausible prose that is nowhere in this EPUB.
ABSENT_PREVIEWS = [
    "Gwendolyn felt herself smile slightly at the thought.",
    "The airship strained against her moorings in the gale.",
    "Captain Grimm considered the etherealist for a long moment.",
    "Rowl the cat regarded the humans with frank contempt.",
    "Bridget hefted the copper vat and started up the stairs.",
    "The Spirearch listened without interrupting once.",
]


def _never_called(path):
    """Stand-in for a parse the audit must not perform.

    The audit catches parse failures by design, so this raise alone would be
    swallowed into `text_status == "unreadable"` — which is exactly what the
    caller asserts against `"skipped"`.
    """
    raise AssertionError(f"unexpectedly parsed the EPUB at {path}")


def _points(previews):
    """(chapter, sentence_index, audio_start_ms, preview) tuples."""
    return [
        (i // 3, i % 3, i * 5000, text)
        for i, text in enumerate(previews)
    ]


async def _seed_pair(db, tmp_path, *, filename="book.epub", docs=None):
    """An EBook backed by a real EPUB on disk, paired with an audiobook."""
    path = write_epub(
        tmp_path / filename,
        docs or [("c1.xhtml", CH1), ("c2.xhtml", CH2)],
    )
    eb = EBook(title="The Aeronaut's Windlass", filename=filename, file_path=path)
    # The audio path is derived from `filename` so that seeding two pairs in one
    # test gives two distinct audiobooks. `audiobooks.file_path` is unique since
    # issue #256; a fixed "a.m4b" made the second call an IntegrityError.
    audio_name = f"{filename.rsplit('.', 1)[0]}.m4b"
    ab = AudioBook(title="The Aeronaut's Windlass", filename=audio_name,
                   file_path=str(tmp_path / audio_name))
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    return pair, path


async def _map(db, pair, previews, *, epub_file_hash=None):
    return await make_sync_map(
        db, pair.id, _points(previews), epub_file_hash=epub_file_hash
    )


# ---------------------------------------------------------------------------
# The audit service
# ---------------------------------------------------------------------------

async def test_matching_hash_and_present_sentences_is_healthy(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS, epub_file_hash=hash_file(path))

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["pair_id"] == pair.id
    assert row["hash_status"] == "match"
    assert row["sampled"] == len(PRESENT_PREVIEWS)
    assert row["hit_rate"] == 1.0
    assert row["status"] == "healthy"
    assert row["suggested_action"] is None


async def test_hash_mismatch_is_stale(db, tmp_path):
    pair, _path = await _seed_pair(db, tmp_path)
    # A hash from a different file: the map describes a document that is no
    # longer the one on disk, whatever its previews happen to say.
    await _map(db, pair, PRESENT_PREVIEWS, epub_file_hash="0" * 64)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "mismatch"
    assert row["status"] == "stale"
    assert "hash" in row["reason"].lower()


async def test_hash_mismatch_skips_the_text_sample(db, tmp_path, monkeypatch):
    """The verdict is already decided — don't pay to parse the EPUB.

    Parsing every flagged book is the expensive half of a 135-pair audit, and
    the hash has already proved what the text could only suggest.
    """
    pair, _path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS, epub_file_hash="0" * 64)

    from services import epub_parser
    monkeypatch.setattr(epub_parser, "extract_book_text", _never_called)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["text_status"] == "skipped"
    assert row["sampled"] == 0
    assert row["hit_rate"] is None


async def test_null_hash_with_absent_sentences_is_stale(db, tmp_path):
    """Pair 260: a legacy map whose text is nowhere in the current EPUB."""
    pair, _path = await _seed_pair(db, tmp_path)
    await _map(db, pair, ABSENT_PREVIEWS)  # epub_file_hash NULL

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "unknown"
    assert row["sampled"] == len(ABSENT_PREVIEWS)
    assert row["hits"] == 0
    assert row["hit_rate"] == 0.0
    assert row["status"] == "stale"
    assert row["suggested_action"] == "retranscribe"  # no cached transcript


async def test_null_hash_with_present_sentences_is_healthy(db, tmp_path):
    """Pair 84: a legacy map that still describes the file on disk.

    Front-matter offset is absorbed because the needle is looked up in the
    whole-spine text, not in the chapter the point claims.
    """
    pair, _path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "unknown"
    assert row["hit_rate"] == 1.0
    assert row["status"] == "healthy"


async def test_null_hash_reports_unknown_when_sampling_is_off(db, tmp_path):
    """No hash and no evidence gathered ⇒ unknown provenance, not healthy."""
    pair, _path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS)

    [row] = await sync_map_audit.audit_sync_maps(db, sample_size=0)

    assert row["sampled"] == 0
    assert row["status"] == "unknown"
    assert row["suggested_action"] is None


async def test_missing_ebook_file_is_stale(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS, epub_file_hash=hash_file(path))
    import os
    os.remove(path)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "file_missing"
    assert row["status"] == "stale"
    assert row["suggested_action"] == "restore_file"


async def test_cached_transcript_makes_realign_the_suggested_action(db, tmp_path):
    pair, _path = await _seed_pair(db, tmp_path)
    await _map(db, pair, ABSENT_PREVIEWS)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b",
        sentence_count=1, sentences_json="[]",
    ))
    await db.commit()

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["has_cached_transcript"] is True
    assert row["suggested_action"] == "realign"


async def test_sampling_is_bounded_and_spread_across_the_map(db, tmp_path):
    """A live map runs to thousands of points; the audit reads a handful."""
    pair, path = await _seed_pair(db, tmp_path)
    many = [
        (i // 50, i % 50, i * 1000, PRESENT_PREVIEWS[i % len(PRESENT_PREVIEWS)])
        for i in range(500)
    ]
    await make_sync_map(db, pair.id, many, epub_file_hash=hash_file(path))

    [row] = await sync_map_audit.audit_sync_maps(db, sample_size=7)

    assert row["sampled"] == 7
    assert row["total_sentences"] == 500
    assert row["status"] == "healthy"


async def test_points_with_no_usable_preview_are_not_counted(db, tmp_path):
    """NULL and too-short previews carry no evidence either way."""
    pair, path = await _seed_pair(db, tmp_path)
    points = [
        (0, 0, 0, None),
        (0, 1, 1000, "Yes."),
        (0, 2, 2000, "The harbour lay still under a flat grey sky."),
    ]
    await make_sync_map(db, pair.id, points, epub_file_hash=hash_file(path))

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["sampled"] == 1
    assert row["hit_rate"] == 1.0


async def test_a_map_with_no_usable_previews_gathers_no_evidence(db, tmp_path):
    """Every preview NULL or too short — sampling yields nothing to judge on."""
    pair, _path = await _seed_pair(db, tmp_path)
    await make_sync_map(db, pair.id, [
        (0, 0, 0, None),
        (0, 1, 1000, "Yes."),
        (0, 2, 2000, "No."),
    ])

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["sampled"] == 0
    assert row["hit_rate"] is None
    assert row["status"] == "unknown"


async def test_matching_hash_alone_is_healthy_without_sampling(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await _map(db, pair, ABSENT_PREVIEWS, epub_file_hash=hash_file(path))

    [row] = await sync_map_audit.audit_sync_maps(db, sample_size=0)

    assert row["sampled"] == 0
    assert row["status"] == "healthy"


async def test_unhashable_ebook_path_is_treated_as_missing(db, tmp_path):
    """A path that exists but cannot be opened — a directory, a file the process
    has no read permission on — must not take the whole audit down."""
    pair, _path = await _seed_pair(db, tmp_path)
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    ebook = await db.get(EBook, pair.ebook_id)
    ebook.file_path = str(directory)
    await db.commit()
    await _map(db, pair, PRESENT_PREVIEWS)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "file_missing"
    assert row["status"] == "stale"


async def test_limit_bounds_how_many_pairs_are_audited(db, tmp_path):
    pair_a, path_a = await _seed_pair(db, tmp_path, filename="a.epub")
    pair_b, path_b = await _seed_pair(db, tmp_path, filename="b.epub")
    await _map(db, pair_a, PRESENT_PREVIEWS, epub_file_hash=hash_file(path_a))
    await _map(db, pair_b, PRESENT_PREVIEWS, epub_file_hash=hash_file(path_b))

    rows = await sync_map_audit.audit_sync_maps(db, limit=1)

    assert [r["pair_id"] for r in rows] == [pair_a.id]


async def test_unreadable_ebook_reports_rather_than_raising(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS)
    with open(path, "wb") as f:
        f.write(b"not a zip archive at all")

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["text_status"] == "unreadable"
    assert row["status"] == "unknown"


async def test_pairs_without_a_sync_map_are_not_audited(db, tmp_path):
    await _seed_pair(db, tmp_path)

    assert await sync_map_audit.audit_sync_maps(db) == []


async def test_single_pair_can_be_audited(db, tmp_path):
    pair_a, path_a = await _seed_pair(db, tmp_path, filename="a.epub")
    pair_b, path_b = await _seed_pair(db, tmp_path, filename="b.epub")
    await _map(db, pair_a, PRESENT_PREVIEWS, epub_file_hash=hash_file(path_a))
    await _map(db, pair_b, PRESENT_PREVIEWS, epub_file_hash=hash_file(path_b))

    rows = await sync_map_audit.audit_sync_maps(db, pair_id=pair_b.id)

    assert [r["pair_id"] for r in rows] == [pair_b.id]


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_client(make_client):
    from routers import troubleshoot
    return lambda: make_client(troubleshoot.router)


async def test_endpoint_reports_flagged_pairs(
    db, tmp_path, audit_client, make_user, auth_header
):
    editor = await make_user(role="editor")
    good, good_path = await _seed_pair(db, tmp_path, filename="good.epub")
    bad, _bad_path = await _seed_pair(db, tmp_path, filename="bad.epub")
    await _map(db, good, PRESENT_PREVIEWS, epub_file_hash=hash_file(good_path))
    await _map(db, bad, ABSENT_PREVIEWS)
    db.add(AudioTranscript(pair_id=bad.id, audiobook_path="/x/a.m4b",
                           sentence_count=1, sentences_json="[]"))
    await db.commit()

    async with audit_client() as c:
        r = await c.get("/api/troubleshoot/sync-map-audit",
                        headers=auth_header(editor))

    assert r.status_code == 200
    body = r.json()
    assert body["checked"] == 2
    assert body["flagged"] == 1
    assert body["realign_endpoint"] == "/api/transcription/{pair_id}/realign"
    by_id = {row["pair_id"]: row for row in body["pairs"]}
    assert by_id[good.id]["status"] == "healthy"
    assert by_id[bad.id]["status"] == "stale"
    assert by_id[bad.id]["realign_path"] == f"/api/transcription/{bad.id}/realign"


async def test_endpoint_can_return_only_flagged_pairs(
    db, tmp_path, audit_client, make_user, auth_header
):
    editor = await make_user(role="editor")
    good, good_path = await _seed_pair(db, tmp_path, filename="good.epub")
    bad, _bad_path = await _seed_pair(db, tmp_path, filename="bad.epub")
    await _map(db, good, PRESENT_PREVIEWS, epub_file_hash=hash_file(good_path))
    await _map(db, bad, ABSENT_PREVIEWS)

    async with audit_client() as c:
        r = await c.get("/api/troubleshoot/sync-map-audit?flagged_only=true",
                        headers=auth_header(editor))

    assert r.status_code == 200
    assert [row["pair_id"] for row in r.json()["pairs"]] == [bad.id]
    assert r.json()["checked"] == 2


async def test_endpoint_rejects_a_plain_user(
    db, tmp_path, audit_client, make_user, auth_header
):
    """Same gate as its mutating troubleshoot siblings — an audit walks every
    ebook on disk, which is not a read a viewer account gets to trigger."""
    viewer = await make_user(role="user")
    pair, path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS, epub_file_hash=hash_file(path))

    async with audit_client() as c:
        r = await c.get("/api/troubleshoot/sync-map-audit",
                        headers=auth_header(viewer))

    assert r.status_code == 403


async def test_endpoint_requires_authentication(db, audit_client):
    async with audit_client() as c:
        r = await c.get("/api/troubleshoot/sync-map-audit")

    assert r.status_code == 401
