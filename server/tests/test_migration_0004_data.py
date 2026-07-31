"""
Migration 0004's data steps, exercised without Postgres.

The DDL half of 0004 needs a real Postgres and is covered by the migrations CI
job. The *data* half is what can silently lose reading positions, and it is
plain portable SQL plus a pure remap, so it is tested here.

Two things have to hold:

1. Every existing bookmark comes out of the migration holding an
   axis-independent anchor — a text preview, or failing that a percent.
   A bookmark with neither is unresolvable by the restore ladder afterwards,
   i.e. a lost position.
2. The rescue happens BEFORE sync points are re-based onto spine indices.
   Run in the other order, the preview lookup reads chapters that have already
   moved and returns the wrong sentence — which would be worse than losing it,
   because the position would look fine and be wrong.
"""

import importlib.util
import os
import zipfile

import pytest
from sqlalchemy import text

from models.book import BookPair
from models.bookmark import Bookmark, BookmarkSource
from models.progress import ProgressType, UserProgress
from models.sync_map import SyncMap, SyncPoint
from tests.factories import make_book_pair

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic", "versions", "0004_canonical_position.py",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0004", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enum_is_declared_so_create_table_wont_duplicate_it():
    """Native Postgres enums must be declared `create_type=False`.

    Without it, `op.create_table` emits its own `CREATE TYPE` on top of the
    explicit `.create()`, and the whole migration dies with
    `DuplicateObject: type "hintkind" already exists`. 0001_baseline documents
    this in a comment; 0004 shipped ignoring it and broke the migrations job.

    Asserted on the type object because the failure itself only reproduces
    against a real Postgres, which the default suite has no access to.
    """
    migration = _load_migration()
    assert migration.hintkind.create_type is False


PROSE_A = ("<html><body><p>The harbour lay still under a flat grey sky. "
           "Althea counted the ships at anchor and found one missing.</p></body></html>")
PROSE_B = ("<html><body><p>Brashen kept his own counsel on the matter. "
           "The crew had seen worse crossings than this one.</p></body></html>")
BLANK = "<html><body></body></html>"


def _write_epub(path, docs):
    manifest = "".join(
        f'<item id="id{i}" href="d{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(docs))
    )
    spine = "".join(f'<itemref idref="id{i}"/>' for i in range(len(docs)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="b">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title>'
        '<dc:identifier id="b">urn:uuid:x</dc:identifier><dc:language>en</dc:language></metadata>'
        f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for i, html in enumerate(docs):
            z.writestr(f"OEBPS/d{i}.xhtml", html)
    return str(path)


async def test_every_bookmark_keeps_a_resolvable_anchor(db, make_user):
    """The load-bearing check: after the rescue, no position is left with
    neither a preview nor a percent."""
    migration = _load_migration()
    user = await make_user(username="reader")
    pair = await make_book_pair(db)

    sm = SyncMap(book_pair_id=pair.id, version=1, total_sentences=2, total_chapters=1)
    db.add(sm)
    await db.flush()
    db.add(SyncPoint(
        sync_map_id=sm.id, epub_chapter=3, epub_sentence_index=7,
        epub_text_preview="althea counted the ships at anchor",
        audio_start_ms=1000, audio_end_ms=4000, confidence=1.0,
    ))
    # A bookmark whose anchor matches a sync point -> rescued by preview.
    db.add(Bookmark(
        user_id=user.id, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
        epub_chapter=3, epub_sentence_index=7, anchor_revision=1, is_completed=False,
    ))
    # Progress gives a percent for the same pair, the coarse backstop.
    db.add(UserProgress(
        user_id=user.id, media_type=ProgressType.EBOOK, ebook_id=pair.ebook_id,
        book_pair_id=pair.id, epub_progress_percent=46.81, is_completed=False,
    ))
    await db.commit()

    await db.run_sync(lambda sync_conn: migration.rescue_bookmark_anchors(sync_conn))
    await db.commit()

    row = (await db.execute(text(
        "SELECT epub_text_preview, epub_progress_percent FROM bookmarks"
    ))).one()
    assert row[0] == "althea counted the ships at anchor"
    assert row[1] == pytest.approx(46.81)


async def test_percent_rescues_a_bookmark_with_no_matching_sync_point(db, make_user):
    """A bookmark whose chapter/sentence hits no sync point still has to come
    out with something the ladder can use."""
    migration = _load_migration()
    user = await make_user(username="reader")
    pair = await make_book_pair(db)

    sm = SyncMap(book_pair_id=pair.id, version=1, total_sentences=1, total_chapters=1)
    db.add(sm)
    await db.flush()
    db.add(SyncPoint(
        sync_map_id=sm.id, epub_chapter=0, epub_sentence_index=0,
        epub_text_preview="something else entirely",
        audio_start_ms=0, audio_end_ms=1000, confidence=1.0,
    ))
    db.add(Bookmark(
        user_id=user.id, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
        epub_chapter=99, epub_sentence_index=99, anchor_revision=1, is_completed=False,
    ))
    db.add(UserProgress(
        user_id=user.id, media_type=ProgressType.EBOOK, ebook_id=pair.ebook_id,
        book_pair_id=pair.id, epub_progress_percent=12.5, is_completed=False,
    ))
    await db.commit()

    await db.run_sync(lambda sync_conn: migration.rescue_bookmark_anchors(sync_conn))
    await db.commit()

    row = (await db.execute(text(
        "SELECT epub_text_preview, epub_progress_percent FROM bookmarks"
    ))).one()
    assert row[0] is None
    assert row[1] == pytest.approx(12.5), "no preview and no percent = a lost position"


async def test_sync_points_are_rebased_onto_spine_indices(db, make_user, tmp_path):
    """The remap itself: old chapter N was the Nth document that produced
    sentences, so a blank front-matter page must push everything after it up."""
    migration = _load_migration()
    await make_user(username="reader")

    # Spine: [blank, prose, prose] -> surviving docs are at spine 1 and 2,
    # but the old numbering called them chapters 0 and 1.
    epub_path = _write_epub(tmp_path / "b.epub", [BLANK, PROSE_A, PROSE_B])
    pair = await make_book_pair(db)
    await db.execute(text("UPDATE ebooks SET file_path = :p WHERE id = :i"),
                     {"p": epub_path, "i": pair.ebook_id})

    sm = SyncMap(book_pair_id=pair.id, version=1, total_sentences=2, total_chapters=2)
    db.add(sm)
    await db.flush()
    db.add_all([
        SyncPoint(sync_map_id=sm.id, epub_chapter=0, epub_sentence_index=0,
                  epub_text_preview="the harbour lay still",
                  audio_start_ms=0, audio_end_ms=1000, confidence=1.0),
        SyncPoint(sync_map_id=sm.id, epub_chapter=1, epub_sentence_index=0,
                  epub_text_preview="brashen kept his own counsel",
                  audio_start_ms=2000, audio_end_ms=3000, confidence=1.0),
    ])
    await db.commit()

    await db.run_sync(lambda sync_conn: migration._remap_sync_points(sync_conn))
    await db.commit()

    chapters = [r[0] for r in (await db.execute(text(
        "SELECT epub_chapter FROM sync_points ORDER BY audio_start_ms"
    ))).all()]
    assert chapters == [1, 2]


async def test_remap_is_a_no_op_when_no_documents_were_skipped(db, make_user, tmp_path):
    migration = _load_migration()
    await make_user(username="reader")

    epub_path = _write_epub(tmp_path / "b.epub", [PROSE_A, PROSE_B])
    pair = await make_book_pair(db)
    await db.execute(text("UPDATE ebooks SET file_path = :p WHERE id = :i"),
                     {"p": epub_path, "i": pair.ebook_id})

    sm = SyncMap(book_pair_id=pair.id, version=1, total_sentences=2, total_chapters=2)
    db.add(sm)
    await db.flush()
    db.add_all([
        SyncPoint(sync_map_id=sm.id, epub_chapter=0, epub_sentence_index=0,
                  epub_text_preview="a", audio_start_ms=0, audio_end_ms=1, confidence=1.0),
        SyncPoint(sync_map_id=sm.id, epub_chapter=1, epub_sentence_index=0,
                  epub_text_preview="b", audio_start_ms=2, audio_end_ms=3, confidence=1.0),
    ])
    await db.commit()

    await db.run_sync(lambda sync_conn: migration._remap_sync_points(sync_conn))
    await db.commit()

    chapters = [r[0] for r in (await db.execute(text(
        "SELECT epub_chapter FROM sync_points ORDER BY audio_start_ms"
    ))).all()]
    assert chapters == [0, 1]


async def test_a_missing_ebook_file_leaves_chapters_untouched(db, make_user):
    """Books whose file is gone must be skipped, not corrupted."""
    migration = _load_migration()
    await make_user(username="reader")
    pair = await make_book_pair(db)
    await db.execute(text("UPDATE ebooks SET file_path = :p WHERE id = :i"),
                     {"p": "/nonexistent/gone.epub", "i": pair.ebook_id})

    sm = SyncMap(book_pair_id=pair.id, version=1, total_sentences=1, total_chapters=1)
    db.add(sm)
    await db.flush()
    db.add(SyncPoint(sync_map_id=sm.id, epub_chapter=5, epub_sentence_index=0,
                     epub_text_preview="x", audio_start_ms=0, audio_end_ms=1, confidence=1.0))
    await db.commit()

    await db.run_sync(lambda sync_conn: migration._remap_sync_points(sync_conn))
    await db.commit()

    chapter = (await db.execute(text("SELECT epub_chapter FROM sync_points"))).scalar_one()
    assert chapter == 5
