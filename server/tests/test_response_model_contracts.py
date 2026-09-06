"""
Response-shape contracts for the routes typed under issue #258.

Each route here used to hand back a bare dict. The web client reads named keys
off those dicts (`web/src/api/troubleshoot.js`, `web/src/api/library.js` and
the pages behind them), and nothing on the server pinned them — a renamed key
was a silent break found in the browser.

Two layers per route, deliberately independent of each other:

1. The **exact key set** of the JSON body, written out by hand. This does not
   go through the schema, so editing the schema and the handler together to
   drop a key the web reads still fails here.
2. ``schemas.<Model>.model_validate(body)``. The schemas carry
   ``extra="forbid"`` (see ``schemas.StrictResponse``), so this — and the
   response validation FastAPI does on the way out — fails in *both*
   directions: a key the handler stops sending, and a key it starts sending
   that the schema does not know. Nothing is stripped silently.

The literal key sets are the contract. Change them only with the web client.
"""

import os
import subprocess

import pytest

import schemas
from config import settings
from models.book import AudioBook, BookPair, EBook, PairStatus
from models.library_issue import MultiFileAudiobookFolder
from routers import library, troubleshoot
from services import chapter_repair, library_verify
from services.file_hash import hash_file
from tests.test_sync_map_drift_audit import PRESENT_PREVIEWS, _map, _seed_pair

# --- troubleshoot -----------------------------------------------------------

# `troubleshoot._item_dict` — ebook/audiobook rows in most categories.
ITEM_KEYS = {
    "item_type", "item_id", "title", "author", "filename", "file_path",
    "format", "file_size", "detail", "pair_id",
}
# `multi_file_audiobook` rows: the item shape plus the folder's own fields.
FOLDER_KEYS = ITEM_KEYS | {"file_count", "extension", "imported_track_count"}
# `failed_transcription` / `sync_map_missing`: keyed by pair, not item.
PAIR_KEYS = {"pair_id", "ebook_id", "audiobook_id", "title", "author", "detail"}
# `orphaned_cover` / `failed_acsm`: a loose file, no DB row behind it.
FILE_KEYS = {"filename", "file_path", "file_size", "detail"}

# Same set as `CATEGORIES` in `web/src/pages/TroubleshootPage.jsx`.
CATEGORY_KEYS = {
    "missing", "zero_byte", "chapter_encoding_bad", "audio_corrupt", "ebook_drm",
    "ebook_unreadable", "unsupported_format", "multi_file_audiobook",
    "sync_map_missing", "duplicate", "missing_cover", "orphaned_cover",
    "failed_transcription", "failed_acsm",
}

SCAN_PROGRESS_KEYS = {
    "running", "phase_index", "phase_count", "phase_label", "current", "total",
    "started_at", "finished_at", "cancel_requested", "last_error",
}

AUDIT_KEYS = {"sample_size", "checked", "flagged", "realign_endpoint", "pairs"}
AUDIT_ROW_KEYS = {
    "pair_id", "title", "ebook_id", "ebook_path", "sync_map_version",
    "total_sentences", "stored_epub_hash", "current_epub_hash", "hash_status",
    "text_status", "sampled", "hits", "hit_rate", "has_cached_transcript",
    "status", "reason", "suggested_action", "realign_path",
}

# --- library ----------------------------------------------------------------

VERIFY_KEYS = {"orphaned_ebooks", "orphaned_audiobooks", "truncated"}
VERIFY_ROW_KEYS = {"id", "title", "author", "filename", "file_path", "format"}
CLEANUP_KEYS = {"message", "deleted_ebooks", "deleted_audiobooks"}


def _validate(model_name: str, body):
    """Round-trip `body` through `schemas.<model_name>`.

    Looked up by name so a missing model reads as "this schema does not exist
    yet" rather than an import error that takes the whole module down.
    """
    model = getattr(schemas, model_name, None)
    assert model is not None, f"schemas.{model_name} is not defined"
    return model.model_validate(body)


async def _ebook(db, path, title="An Ebook"):
    eb = EBook(title=title, author="Someone", filename=os.path.basename(path),
               file_path=str(path), format="epub", file_size=2048)
    db.add(eb)
    await db.commit()
    await db.refresh(eb)
    return eb


async def _audiobook(db, path, title="An Audiobook"):
    ab = AudioBook(title=title, author="Someone", filename=os.path.basename(path),
                   file_path=str(path), format="m4b", file_size=4096)
    db.add(ab)
    await db.commit()
    await db.refresh(ab)
    return ab


@pytest.fixture
async def editor(make_user):
    return await make_user(username="contract-editor", role="editor")


@pytest.fixture
def ts_client(make_client):
    return lambda: make_client(troubleshoot.router)


@pytest.fixture
def lib_client(make_client):
    return lambda: make_client(library.router)


# ===========================================================================
# troubleshoot
# ===========================================================================

async def test_issues_envelope_and_every_item_shape(
    db, ts_client, editor, auth_header, monkeypatch, tmp_path
):
    """One response carrying all four row shapes the page renders."""
    # `missing` → `_item_dict` (file_path points nowhere).
    missing = await _ebook(db, tmp_path / "gone.epub")
    # `failed_transcription` → pair-keyed row.
    audio_path = tmp_path / "present.m4b"
    audio_path.write_bytes(b"x" * 2048)
    ab = await _audiobook(db, audio_path)
    pair = BookPair(ebook_id=missing.id, audiobook_id=ab.id, status=PairStatus.ERROR)
    db.add(pair)
    # `multi_file_audiobook` → folder row.
    db.add(MultiFileAudiobookFolder(
        folder_path=str(tmp_path / "Dune"), extension=".mp3", file_count=12,
        total_size=1234, guessed_title="Dune", guessed_author="Frank Herbert",
        fingerprint="abc", dismissed=False,
    ))
    await db.commit()
    # `orphaned_cover` and `failed_acsm` → file rows.
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "stray.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(settings, "covers_dir", str(covers))
    failed = tmp_path / "imports" / "acsm" / "failed"
    failed.mkdir(parents=True)
    (failed / "broken.acsm").write_bytes(b"<xml/>")
    monkeypatch.setattr(settings, "imports_dir", str(tmp_path / "imports"))
    monkeypatch.setattr(chapter_repair, "check_chapter_encoding", lambda p: (True, None))

    async with ts_client() as c:
        r = await c.get("/api/troubleshoot/issues", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"categories", "counts", "total"}
    assert set(body["categories"]) == CATEGORY_KEYS
    assert set(body["counts"]) == CATEGORY_KEYS
    assert body["total"] == sum(body["counts"].values())

    cats = body["categories"]
    assert [set(row) for row in cats["missing"]] == [ITEM_KEYS]
    assert [set(row) for row in cats["multi_file_audiobook"]] == [FOLDER_KEYS]
    assert [set(row) for row in cats["failed_transcription"]] == [PAIR_KEYS]
    assert [set(row) for row in cats["orphaned_cover"]] == [FILE_KEYS]
    assert [set(row) for row in cats["failed_acsm"]] == [FILE_KEYS]
    # Both books have no cover → two `_item_dict` rows here as well.
    assert {frozenset(row) for row in cats["missing_cover"]} == {frozenset(ITEM_KEYS)}

    _validate("TroubleshootIssues", body)


async def test_scan_progress_shape(ts_client, editor, auth_header):
    async with ts_client() as c:
        r = await c.get("/api/troubleshoot/scan/progress", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    assert set(r.json()) == SCAN_PROGRESS_KEYS
    _validate("LibraryScanProgress", r.json())


async def test_scan_start_and_cancel_shape(ts_client, editor, auth_header, monkeypatch):
    async def _start():
        return True

    monkeypatch.setattr(library_verify, "start_scan", _start)
    monkeypatch.setattr(library_verify, "request_cancel", lambda: None)

    async with ts_client() as c:
        started = await c.post("/api/troubleshoot/scan", headers=auth_header(editor))
        cancelled = await c.post("/api/troubleshoot/scan/cancel", headers=auth_header(editor))

    assert started.status_code == 202, started.text
    assert started.json() == {"status": "started"}
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {"status": "cancel_requested"}
    _validate("ActionResult", started.json())
    _validate("ActionResult", cancelled.json())


async def test_bulk_delete_shape(db, ts_client, editor, auth_header, tmp_path):
    eb = await _ebook(db, tmp_path / "gone.epub")

    async with ts_client() as c:
        r = await c.post(
            "/api/troubleshoot/bulk-delete?delete_file=false",
            json={"items": [{"item_type": "ebook", "item_id": eb.id}]},
            headers=auth_header(editor),
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1}
    _validate("DeletedCount", r.json())


async def test_delete_orphan_covers_shape(ts_client, editor, auth_header, monkeypatch, tmp_path):
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "stray.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(settings, "covers_dir", str(covers))

    async with ts_client() as c:
        r = await c.post(
            "/api/troubleshoot/delete-orphan-covers",
            json={"filenames": ["stray.jpg"]},
            headers=auth_header(editor),
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 1}
    _validate("DeletedCount", r.json())


async def test_acsm_dismiss_shape(ts_client, editor, auth_header, monkeypatch, tmp_path):
    failed = tmp_path / "imports" / "acsm" / "failed"
    failed.mkdir(parents=True)
    (failed / "broken.acsm").write_bytes(b"<xml/>")
    monkeypatch.setattr(settings, "imports_dir", str(tmp_path / "imports"))

    async with ts_client() as c:
        r = await c.post(
            "/api/troubleshoot/acsm-dismiss",
            json={"filename": "broken.acsm"},
            headers=auth_header(editor),
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "dismissed"}
    _validate("ActionResult", r.json())


async def test_requeue_shape(db, ts_client, editor, auth_header, monkeypatch, tmp_path):
    eb = await _ebook(db, tmp_path / "b.epub")
    ab = await _audiobook(db, tmp_path / "b.m4b")
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.ERROR)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)

    import services.queue_manager as queue_manager

    async def _add(pair_ids):
        return None

    monkeypatch.setattr(queue_manager, "add_to_queue", _add)

    async with ts_client() as c:
        r = await c.post(f"/api/troubleshoot/requeue/{pair.id}", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "queued", "pair_id": pair.id}
    _validate("RequeueResult", r.json())


async def test_repair_chapter_encoding_shape(
    db, ts_client, editor, auth_header, monkeypatch, tmp_path
):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"x")
    ab = await _audiobook(db, path)
    monkeypatch.setattr(chapter_repair, "repair_chapter_encoding", lambda p: (True, None))

    async with ts_client() as c:
        r = await c.post(
            f"/api/troubleshoot/repair-chapter-encoding/{ab.id}", headers=auth_header(editor)
        )

    assert r.status_code == 200, r.text
    # `detail` is present and null on success — the page reads `res.detail`
    # on the failure branch, so the key must not come and go.
    assert r.json() == {"status": "repaired", "detail": None, "item_id": ab.id}
    _validate("ChapterRepairResult", r.json())


async def test_bulk_repair_chapter_encoding_shape(
    db, ts_client, editor, auth_header, monkeypatch, tmp_path
):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"x")
    ab = await _audiobook(db, path, title="Stubborn")
    monkeypatch.setattr(chapter_repair, "repair_chapter_encoding", lambda p: (False, "still bad"))

    async with ts_client() as c:
        r = await c.post(
            "/api/troubleshoot/bulk-repair-chapter-encoding",
            json={"item_ids": [ab.id, 999999]},
            headers=auth_header(editor),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"repaired", "failures"}
    assert body["repaired"] == 0
    assert body["failures"] == [
        {"item_id": ab.id, "title": "Stubborn", "error": "still bad"},
        {"item_id": 999999, "title": None, "error": "File not found on disk"},
    ]
    _validate("BulkChapterRepairResult", body)


async def test_replace_file_shape(
    db, ts_client, editor, auth_header, monkeypatch, tmp_path
):
    ebook_dir = tmp_path / "ebooks"
    ebook_dir.mkdir()
    monkeypatch.setattr(settings, "ebook_dir", str(ebook_dir))

    async def _fake_extract(filepath, file_type, db, library_root=None):
        return {}

    monkeypatch.setattr(library, "extract_metadata", _fake_extract)
    old = ebook_dir / "old.epub"
    old.write_bytes(b"the old parse")
    eb = await _ebook(db, old)

    async with ts_client() as c:
        r = await c.post(
            f"/api/troubleshoot/replace/ebook/{eb.id}",
            headers=auth_header(editor),
            files={"file": ("new.epub", b"the new parse", "application/octet-stream")},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"status", "integrity_ok", "detail", "item_id"}
    assert body["status"] == "replaced"
    assert body["item_id"] == eb.id
    assert isinstance(body["integrity_ok"], bool)
    _validate("ReplaceFileResult", body)


async def test_multi_file_dismiss_and_remove_tracks_shape(
    db, ts_client, editor, auth_header, tmp_path
):
    row = MultiFileAudiobookFolder(
        folder_path=str(tmp_path / "Dune"), extension=".mp3", file_count=12,
        total_size=1234, fingerprint="abc", dismissed=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    async with ts_client() as c:
        removed = await c.post(
            f"/api/troubleshoot/multi-file/{row.id}/remove-tracks", headers=auth_header(editor)
        )
        dismissed = await c.post(
            f"/api/troubleshoot/multi-file/{row.id}/dismiss", headers=auth_header(editor)
        )

    assert removed.status_code == 200, removed.text
    assert removed.json() == {"deleted": 0, "id": row.id}
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json() == {"status": "dismissed", "id": row.id}
    _validate("MultiFileRemoveTracksResult", removed.json())
    _validate("MultiFileDismissResult", dismissed.json())


async def test_sync_map_audit_shape(db, ts_client, editor, auth_header, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await _map(db, pair, PRESENT_PREVIEWS, epub_file_hash=hash_file(path))

    async with ts_client() as c:
        r = await c.get("/api/troubleshoot/sync-map-audit", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == AUDIT_KEYS
    assert [set(row) for row in body["pairs"]] == [AUDIT_ROW_KEYS]
    _validate("SyncMapAuditResponse", body)


# ===========================================================================
# library
# ===========================================================================

async def test_verify_shape(db, lib_client, editor, auth_header, tmp_path):
    await _ebook(db, tmp_path / "gone.epub")
    await _audiobook(db, tmp_path / "gone.m4b")

    async with lib_client() as c:
        r = await c.get("/api/library/verify", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == VERIFY_KEYS
    assert [set(row) for row in body["orphaned_ebooks"]] == [VERIFY_ROW_KEYS]
    assert [set(row) for row in body["orphaned_audiobooks"]] == [VERIFY_ROW_KEYS]
    _validate("VerifyFilesResponse", body)


async def test_cleanup_shape(db, lib_client, editor, auth_header, tmp_path):
    eb = await _ebook(db, tmp_path / "gone.epub")

    async with lib_client() as c:
        r = await c.post(
            "/api/library/cleanup",
            json={"ebook_ids": [eb.id], "audiobook_ids": []},
            headers=auth_header(editor),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == CLEANUP_KEYS
    assert body["deleted_ebooks"] == 1
    assert body["deleted_audiobooks"] == 0
    assert isinstance(body["message"], str)
    _validate("CleanupResponse", body)


async def test_rescan_all_shape(lib_client, editor, auth_header):
    async with lib_client() as c:
        r = await c.post("/api/library/rescan-all", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    assert set(r.json()) == {"message"}
    _validate("MessageResponse", r.json())


class _CalibreFound:
    returncode = 0
    stdout = "calibre 7.0.0\n"
    stderr = ""


async def test_calibre_status_shape_when_available(lib_client, editor, auth_header, monkeypatch):
    monkeypatch.setattr(library.subprocess, "run", lambda *a, **k: _CalibreFound())

    async with lib_client() as c:
        r = await c.get("/api/library/calibre-status", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    # No `error` key on the happy path — the System tile shows `status.error`
    # as the reason text whenever it is present.
    assert r.json() == {"available": True, "version": "calibre 7.0.0"}
    _validate("CalibreStatusResponse", r.json())


async def test_calibre_status_shape_when_missing(lib_client, editor, auth_header, monkeypatch):
    def _missing(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ebook-convert", timeout=10)

    monkeypatch.setattr(library.subprocess, "run", _missing)

    async with lib_client() as c:
        r = await c.get("/api/library/calibre-status", headers=auth_header(editor))

    assert r.status_code == 200, r.text
    # And no `version` key when it is not there to report.
    assert r.json() == {"available": False, "error": "version check timed out"}
    _validate("CalibreStatusResponse", r.json())
