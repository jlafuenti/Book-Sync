"""
Shared test-data factories.

Async helpers that seed rows into the SQLite test DB. Import these instead of
re-implementing seeding per test file. (User creation lives in the `make_user`
fixture in conftest.py.)
"""

import itertools
import struct
import zipfile

from sqlalchemy import select, text

from models.book import EBook, AudioBook, BookPair, PairStatus
from models.sync_map import SyncMap, SyncPoint

_path_seq = itertools.count(1)


def _unique_path(filename):
    """A distinct `/x/...` path per call.

    `ebooks.file_path` and `audiobooks.file_path` are unique since issue #256.
    A factory that always minted `/x/e.epub` would turn "seed two books", which
    dozens of tests do, into an IntegrityError. The paths point at nothing on
    disk either way; only their distinctness matters.
    """
    return f"/x/{next(_path_seq)}/{filename}"


def write_epub(path, spine_docs, *, manifest_order=None):
    """Build a minimal but valid EPUB at `path` and return its path as a str.

    `spine_docs` is [(filename, html)] in spine order. `manifest_order` lets a
    test declare the manifest in a *different* order than the spine — the case
    that separates a spine walk from a manifest walk.
    """
    names = [name for name, _ in spine_docs]
    manifest_names = manifest_order or names
    manifest = "".join(
        f'<item id="id{names.index(n)}" href="{n}" media-type="application/xhtml+xml"/>'
        for n in manifest_names
    )
    spine = "".join(f'<itemref idref="id{i}"/>' for i in range(len(names)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Axis Test</dc:title><dc:identifier id="bookid">urn:uuid:axis</dc:identifier>'
        '<dc:language>en</dc:language></metadata>'
        f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for name, html in spine_docs:
            z.writestr(f"OEBPS/{name}", html)
    return str(path)


def write_minimal_mp3(path):
    """Write a real, tiny MP3 stream (no tags) that mutagen can open.

    `mutagen.mp3.MP3` looks for an MPEG audio sync frame and raises
    `HeaderNotFoundError` on an ID3-only file, so an on-disk fixture for the
    tag-write/re-extract round trip (issue #538) needs actual frame bytes, not
    just a tag block. This is a single MPEG-1 Layer III frame header
    (`0xFFFB9000`: no CRC, 128kbps, 44100Hz, no padding, stereo) padded out to
    its 417-byte frame length and repeated — silence, not decodable audio, but
    enough for mutagen to locate frames and mutagen/ID3 tag reads and writes to
    work against a genuine file rather than a stub.
    """
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(417 - 4)
    with open(path, "wb") as f:
        f.write(frame * 20)
    return str(path)


def write_minimal_m4a(path):
    """Write a real, near-empty M4A/MP4 container that mutagen can open.

    Hand-built rather than pulled from a bundled fixture (mutagen's own test
    audio isn't installed with the package) — just enough ISO-BMFF structure
    for `mutagen.mp4.MP4` to parse and save: `ftyp`, then `moov` holding an
    `mvhd` and a `udta/meta/hdlr+ilst` chain for the tag atoms. No `mdat` or
    `trak` — nothing plays it back, but tag read/write round-trips work
    because mutagen's MP4 tag path never touches sample tables when there
    aren't any.
    """

    def box(tag, payload=b""):
        return struct.pack(">I4s", 8 + len(payload), tag) + payload

    ftyp = box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
    mvhd = box(
        b"mvhd",
        b"\x00" * 4  # version/flags
        + b"\x00" * 4  # creation time
        + b"\x00" * 4  # modification time
        + struct.pack(">I", 1000)  # timescale
        + struct.pack(">I", 0)  # duration
        + struct.pack(">I", 0x00010000)  # rate
        + struct.pack(">H", 0x0100)  # volume
        + b"\x00" * 10  # reserved
        + b"\x00" * 36  # matrix
        + b"\x00" * 24  # predefined
        + struct.pack(">I", 2),  # next track id
    )
    hdlr = box(
        b"hdlr",
        b"\x00" * 4  # version/flags
        + b"\x00" * 4  # predefined
        + b"mdir" + b"appl"
        + b"\x00" * 12  # reserved
        + b"\x00",  # empty name
    )
    meta = box(b"meta", b"\x00\x00\x00\x00" + hdlr + box(b"ilst", b""))
    moov = box(b"moov", mvhd + box(b"udta", meta))
    with open(path, "wb") as f:
        f.write(ftyp + moov)
    return str(path)


async def suspend_user_progress_uniqueness(db):
    """Drop the `user_progress` unique indexes for this test's schema.

    Lets a test reproduce the pre-#64 state — two rows for the same
    (user, media), left by the old GET-creates-a-row race — which the indexes
    now make unrepresentable. The schema is rebuilt per test, so this affects
    nothing else.
    """
    await db.execute(text("DROP INDEX ux_user_progress_user_ebook"))
    await db.execute(text("DROP INDEX ux_user_progress_user_audiobook"))


async def suspend_file_path_uniqueness(db):
    """Drop the `ebooks`/`audiobooks` unique path indexes for this test's schema.

    Lets a test reproduce the pre-#256 state — two rows for the same file, which
    a restore from an older dump or a manual insert could leave behind — that
    the indexes now make unrepresentable. The schema is rebuilt per test, so
    this affects nothing else.
    """
    await db.execute(text("DROP INDEX ux_ebooks_file_path"))
    await db.execute(text("DROP INDEX ux_audiobooks_file_path"))


async def suspend_book_pair_uniqueness(db):
    """Rebuild `book_pairs` without any of its pair-uniqueness for this test's
    schema: neither the one-to-one unique indexes (`ux_book_pairs_ebook_id`,
    `ux_book_pairs_audiobook_id`, issue #691) nor the older composite
    `uq_book_pairs_pair` this replaced (it no longer exists on the model, but
    the discard loop below is harmless if a future model change brings a
    `UniqueConstraint` back).

    SQLite has no `ALTER TABLE ... DROP CONSTRAINT`, and a `UniqueConstraint`
    declared in `CREATE TABLE` becomes an undroppable implicit index — so the
    table is recreated from the ORM definition with the constraint/indexes
    removed. Lets a test reproduce a duplicate pairing (same ebook or
    audiobook in two rows, or the exact same combination twice), which
    `create_pair` has always rejected with a 409 but which a migration or a
    restore from an older dump still has to survive.

    Call before seeding: the existing (empty) table is dropped.
    """
    from sqlalchemy import Index, MetaData, UniqueConstraint
    from models.book import BookPair

    # The two referenced tables come along so the copied FKs still resolve;
    # only `book_pairs` is then created.
    scratch = MetaData()
    EBook.__table__.to_metadata(scratch)
    AudioBook.__table__.to_metadata(scratch)
    unconstrained = BookPair.__table__.to_metadata(scratch)
    for constraint in list(unconstrained.constraints):
        if isinstance(constraint, UniqueConstraint) and constraint.name == "uq_book_pairs_pair":
            unconstrained.constraints.discard(constraint)
    for index in list(unconstrained.indexes):
        if index.unique:
            unconstrained.indexes.discard(index)

    await db.execute(text("DROP TABLE book_pairs"))
    await db.run_sync(lambda session: unconstrained.create(session.connection()))


async def ensure_users(db, *user_ids):
    """Insert placeholder `User` rows with these exact ids, if absent.

    Tests that build `Bookmark` / `UserProgress` rows by hand hard-code
    `user_id=1`. That was free while the SQLite harness ran with
    `PRAGMA foreign_keys=OFF`; enforcement is on now (issue #198), so the
    parent row has to exist. Use the `make_user` fixture instead when the test
    needs a real, loggable account — this is only for the id.
    """
    from models.user import User

    for user_id in user_ids or (1,):
        existing = (await db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if existing is not None:
            continue
        db.add(User(
            id=user_id,
            username=f"fixture-user-{user_id}",
            email=f"fixture-user-{user_id}@example.com",
            hashed_password="not-a-real-hash",
            role="user",
        ))
    await db.commit()


async def make_ebook(db, *, title="E", filename="e.epub", **fields):
    """Create a standalone (unpaired) EBook and return it.

    `**fields` sets any other column directly (`author`, `series`,
    `series_index`, `file_hash`, `auto_pair_excluded_hashes`, …) — the
    auto-pairing tests in `test_auto_match.py` need the metadata the matcher
    reads, and enumerating it as keywords here would just duplicate the model.
    """
    eb = EBook(title=title, filename=filename, file_path=_unique_path(filename),
               **fields)
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    return eb


async def make_audiobook(db, *, title="A", filename="a.m4b", **fields):
    """Create a standalone (unpaired) AudioBook and return it.

    `**fields` sets any other column directly — see `make_ebook`.
    """
    ab = AudioBook(title=title, filename=filename, file_path=_unique_path(filename),
                   **fields)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


async def make_book_pair(db, status=PairStatus.SYNCED, *,
                         ebook_title="E", audiobook_title="A",
                         duration_seconds=None):
    """Create an EBook + AudioBook + BookPair and return the committed pair.

    `duration_seconds` lands on the AudioBook (None = unknown length, which
    is what a freshly scanned file has until ffprobe runs)."""
    eb = EBook(title=ebook_title, filename="e.epub", file_path=_unique_path("e.epub"))
    ab = AudioBook(title=audiobook_title, filename="a.m4b",
                   file_path=_unique_path("a.m4b"),
                   duration_seconds=duration_seconds)
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=status)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)
    return pair


# Default sync points (chapter, sentence_index, audio_start_ms, preview).
# ch=1/s=1 has a NULL preview to exercise the nearest-preview fallback.
DEFAULT_SYNC_POINTS = [
    (0, 0, 0, "chapter one opening line"),
    (0, 1, 5000, "second sentence here"),
    (1, 0, 10000, "chapter two begins now"),
    (1, 1, 15000, None),
    (1, 2, 20000, "later sentence in two"),
]


async def make_sync_map(db, book_pair_id=1, points=None, *, epub_file_hash=None,
                         degraded=False, degraded_reason=None):
    """Create a SyncMap + ordered SyncPoints for a pair and return the map.

    `epub_file_hash` defaults to None — the legacy "unknown provenance" state a
    map written before issue #295 is in. `degraded`/`degraded_reason` default to
    the "not flagged at alignment time" state every map had before issue #586.
    """
    points = DEFAULT_SYNC_POINTS if points is None else points
    chapters = {ch for ch, *_ in points}
    sm = SyncMap(book_pair_id=book_pair_id, version=1,
                 total_sentences=len(points), total_chapters=len(chapters),
                 epub_file_hash=epub_file_hash,
                 degraded=degraded, degraded_reason=degraded_reason)
    db.add(sm)
    await db.flush()
    for ch, si, ms, preview in points:
        db.add(SyncPoint(
            sync_map_id=sm.id, epub_chapter=ch, epub_sentence_index=si,
            epub_text_preview=preview, audio_start_ms=ms, audio_end_ms=ms + 3000,
            confidence=1.0,
        ))
    await db.commit()
    return sm


async def seed_standalone_position(db, user_id, scope, ident, body):
    """A book-scope position on a book that is half of a pair, as legacy data.

    Since issue #720, `PUT /api/sync/position/{ebook|audiobook}/{id}` folds a
    write on a paired book onto its pair, so the endpoint can no longer create
    this state. Live databases still hold it (236 such rows on one instance,
    written before the fold), and the reset, unpair, replace and convert paths
    must keep handling it, so their tests seed it here, through the same
    `apply_position` the endpoint used, with the book's own scope forced.
    """
    from schemas import PositionScope, PositionUpdate
    from services.position_service import ScopeRef, apply_position

    kind = PositionScope(scope)
    ref = ScopeRef(
        kind,
        ebook_id=ident if kind == PositionScope.EBOOK else None,
        audiobook_id=ident if kind == PositionScope.AUDIOBOOK else None,
    )
    record, accepted = await apply_position(db, user_id, ref, PositionUpdate(**body))
    assert accepted, "seeding a standalone position should never be stale"
    return record
