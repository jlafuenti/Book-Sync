"""
Shared test-data factories.

Async helpers that seed rows into the SQLite test DB. Import these instead of
re-implementing seeding per test file. (User creation lives in the `make_user`
fixture in conftest.py.)
"""

import itertools
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
    """Rebuild `book_pairs` without `uq_book_pairs_pair` for this test's schema.

    SQLite has no `ALTER TABLE ... DROP CONSTRAINT`, and a `UniqueConstraint`
    declared in `CREATE TABLE` becomes an undroppable implicit index — so the
    table is recreated from the ORM definition with that one constraint removed.
    Lets a test reproduce a duplicate pairing, which `create_pair` has always
    rejected with a 409 but which migration 0011 still has to survive.

    Call before seeding: the existing (empty) table is dropped.
    """
    from sqlalchemy import MetaData, UniqueConstraint
    from models.book import BookPair

    # The two referenced tables come along so the copied FKs still resolve;
    # only `book_pairs` is then created.
    scratch = MetaData()
    EBook.__table__.to_metadata(scratch)
    AudioBook.__table__.to_metadata(scratch)
    unconstrained = BookPair.__table__.to_metadata(scratch)
    for constraint in list(unconstrained.constraints):
        if (isinstance(constraint, UniqueConstraint)
                and constraint.name == "uq_book_pairs_pair"):
            unconstrained.constraints.discard(constraint)

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


async def make_ebook(db, *, title="E", filename="e.epub"):
    """Create a standalone (unpaired) EBook and return it."""
    eb = EBook(title=title, filename=filename, file_path=_unique_path(filename))
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    return eb


async def make_audiobook(db, *, title="A", filename="a.m4b"):
    """Create a standalone (unpaired) AudioBook and return it."""
    ab = AudioBook(title=title, filename=filename, file_path=_unique_path(filename))
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


async def make_sync_map(db, book_pair_id=1, points=None, *, epub_file_hash=None):
    """Create a SyncMap + ordered SyncPoints for a pair and return the map.

    `epub_file_hash` defaults to None — the legacy "unknown provenance" state a
    map written before issue #295 is in.
    """
    points = DEFAULT_SYNC_POINTS if points is None else points
    chapters = {ch for ch, *_ in points}
    sm = SyncMap(book_pair_id=book_pair_id, version=1,
                 total_sentences=len(points), total_chapters=len(chapters),
                 epub_file_hash=epub_file_hash)
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
