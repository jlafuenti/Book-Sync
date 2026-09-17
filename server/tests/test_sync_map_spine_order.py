"""
Spine-order check (issue #595, part 3).

A sync map written before EPUB parsing was spine-indexed could number its
chapters by something other than the actual spine order — the issue's own
example is a filename lexicographic sort: `part1, part10, part11, part12,
part2 ... part9` instead of the true reading order `part1, part2 ...
part12`. Migration 0004 re-based every map that existed when it ran, but a
map built since, from a source whose numbering disagreed with the spine in a
way that migration didn't anticipate, is not caught by it.

This adds a fourth, independent signal to the audit: for a sample of a map's
stored points, is the *chapter* that text is really in today (per a fresh,
spine-ordered EPUB parse) consistent, in the map's own stored chapter order,
with an increasing walk through the spine? Independent of the timing check —
it needs only the EPUB file, not a cached transcript.
"""
import json

from models.book import AudioBook, BookPair, EBook, PairStatus
from services import sync_map_audit
from services.file_hash import hash_file
from tests.factories import make_sync_map, write_epub

_WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
          "kilo lima mike november").split()


def _chapter_sentence(i):
    """A long, distinct-vocabulary sentence naming its own true position —
    unambiguously locatable, like the alignment tests' fixtures."""
    return " ".join(_WORDS[(i + j) % len(_WORDS)] + str(i * 11 + j) for j in range(10))


async def _seed_lexicographically_misnumbered_pair(db, tmp_path, n=12):
    """An EPUB whose spine is `part1.xhtml, part2.xhtml, ... part{n}.xhtml`
    (true reading order), paired with a sync map whose stored `epub_chapter`
    instead follows the *lexicographic* sort of those filenames — the exact
    shape the issue describes (`part1, part10, part11, part12, part2 ...`).
    """
    docs = [(f"part{i + 1}.xhtml", f"<html><body><p>{_chapter_sentence(i)}.</p></body></html>")
            for i in range(n)]
    path = write_epub(tmp_path / "book.epub", docs)
    eb = EBook(title="Axis Test", filename="book.epub", file_path=path,
               file_hash=hash_file(path))
    audio_path = tmp_path / "book.m4b"
    audio_path.write_bytes(b"placeholder audio bytes")
    ab = AudioBook(title="Axis Test", filename=audio_path.name,
                   file_path=str(audio_path), file_hash=hash_file(str(audio_path)))
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)

    # The bug: `stored_chapter[true_spine_index]` follows a filename sort,
    # not the spine. `sorted(range(n), key=lambda i: f"part{i+1}")` reproduces
    # exactly that string sort ("part1" < "part10" < "part11" < "part2" ...).
    lex_order = sorted(range(n), key=lambda i: f"part{i + 1}")
    stored_chapter_of = {true_idx: stored for stored, true_idx in enumerate(lex_order)}

    points = [
        (stored_chapter_of[i], 0, i * 180_000, _chapter_sentence(i))
        for i in range(n)
    ]
    # Points must be inserted in the *map's own stored* book order — matching
    # how a real map is walked (`epub_chapter, epub_sentence_index`).
    points.sort(key=lambda p: (p[0], p[1]))
    await make_sync_map(db, pair.id, points, epub_file_hash=hash_file(path))
    return pair, path


async def test_lexicographically_misnumbered_map_is_flagged(db, tmp_path):
    pair, path = await _seed_lexicographically_misnumbered_pair(db, tmp_path)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "match"
    assert row["spine_order_status"] == "mismatch"
    assert row["spine_order_violation_run"] >= 3
    assert row["spine_order_chapters"] is not None
    assert row["status"] == "stale"
    assert row["suggested_action"] == "retranscribe"  # no cached transcript
    assert "spine" in row["reason"].lower()


async def test_lexicographically_misnumbered_map_suggests_realign_with_a_cached_transcript(
    db, tmp_path
):
    """With a cached transcript, re-aligning (cheap, no re-transcription)
    rebuilds the map with today's correct spine numbering — retranscribing
    would be needless Jetson time for a problem that's purely about chapter
    labeling, not the audio or the transcript."""
    from models.transcript import AudioTranscript

    pair, path = await _seed_lexicographically_misnumbered_pair(db, tmp_path)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=12,
        sentences_json=json.dumps([
            {"text": _chapter_sentence(i), "start_ms": i * 180_000,
             "end_ms": i * 180_000 + 3000}
            for i in range(12)
        ]),
    ))
    await db.commit()

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["spine_order_status"] == "mismatch"
    assert row["status"] == "stale"
    assert row["suggested_action"] == "realign"
    assert row["realign_path"] == f"/api/transcription/{pair.id}/realign"


async def test_a_correctly_spine_ordered_map_is_not_flagged(db, tmp_path):
    """The healthy case: stored chapter already matches the spine — nothing
    from this check should trip."""
    n = 12
    docs = [(f"part{i + 1}.xhtml", f"<html><body><p>{_chapter_sentence(i)}.</p></body></html>")
            for i in range(n)]
    path = write_epub(tmp_path / "book.epub", docs)
    eb = EBook(title="Axis Test", filename="book.epub", file_path=path,
               file_hash=hash_file(path))
    audio_path = tmp_path / "book.m4b"
    audio_path.write_bytes(b"placeholder audio bytes")
    ab = AudioBook(title="Axis Test", filename=audio_path.name,
                   file_path=str(audio_path), file_hash=hash_file(str(audio_path)))
    db.add_all([eb, ab])
    await db.flush()
    pair = BookPair(ebook_id=eb.id, audiobook_id=ab.id, status=PairStatus.SYNCED)
    db.add(pair)
    await db.commit()
    await db.refresh(pair)

    points = [(i, 0, i * 180_000, _chapter_sentence(i)) for i in range(n)]
    await make_sync_map(db, pair.id, points, epub_file_hash=hash_file(path))

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["spine_order_status"] == "ok"
    assert row["spine_order_chapters"] is None
    assert row["status"] == "healthy"
    assert row["suggested_action"] is None


async def test_spine_order_check_is_skipped_when_sampling_is_off(db, tmp_path):
    pair, path = await _seed_lexicographically_misnumbered_pair(db, tmp_path)

    [row] = await sync_map_audit.audit_sync_maps(db, sample_size=0)

    assert row["spine_order_status"] == "skipped"
    assert row["status"] == "healthy"
