"""
Sync-map audit timing check (issue #586).

`test_sync_map_drift_audit.py` covers the audit's two long-standing signals
(ebook-file hash and text-sample hit rate) — both about a map built against
the *wrong file*. Neither catches a map built against the *right* file whose
audio content is reordered: `services.alignment`'s anchor filter keeps
timestamps monotonic by dropping a displaced block's anchors and
interpolating over the gap, so the map stays internally plausible while being
minutes wrong for real text, and the audit reported the pair healthy.

This adds a third, independent signal: for a sample of points, find that
point's text in the cached `AudioTranscript` and compare timestamps. Also
covers `sync_maps.degraded` — the companion signal stamped at alignment time
(`services/alignment.py`, `services/sync_engine.py`) — surfacing here too.

The first version of the locator here got it wrong in a way only real data
exposed (see the module docstring in `services/sync_map_audit.py`): it used
`fuzz.token_set_ratio` with no length floor on the haystack side and no
ambiguity check, which on a live pair gave a 95% false mismatch rate against
a true ~11%. That is why every fixture below uses genuinely distinct
sentences (varied vocabulary, not a shared template differing only by a
number) — a template-style near-duplicate set is exactly what the ambiguity
guard exists to refuse to judge, and is covered on its own further down
(`test_near_duplicate_sentences_are_not_falsely_flagged`).
"""

import json

from models.book import AudioBook, BookPair, EBook, PairStatus
from models.transcript import AudioTranscript
from services import sync_map_audit
from services.file_hash import hash_file
from tests.factories import make_sync_map, write_epub

# Distinct vocabulary per sentence (not a shared template with one differing
# number) so the fuzzy locator can find each one confidently and
# unambiguously — a template-style near-duplicate set is covered separately
# below, where "the locator can't confidently place it" is exactly the point.
_WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
          "kilo lima mike november").split()


def _sentence(i):
    return " ".join(_WORDS[(i + j) % len(_WORDS)] + str(i * 7 + j) for j in range(12))


# ---------------------------------------------------------------------------
# `_book_order_violations` (issue #595): a pure function, tested directly.
#
# A naive "did this go backwards from the previous point" check is itself the
# same class of bug this issue is about: one bad locate poisons every
# comparison after it forever if the check just tracks a running max. This
# uses the longest-increasing-subsequence of transcript positions instead
# (mirrors `services.alignment._diagnose_anchor_rejection`'s methodology), so
# a handful of scattered bad locates don't cascade into flagging the rest of
# the book as out of order.
# ---------------------------------------------------------------------------

def test_book_order_violations_empty_for_a_fully_increasing_sequence():
    run, flags = sync_map_audit._book_order_violations([10, 20, 30, 40, 50])
    assert run == 0
    assert flags == [False] * 5


def test_book_order_violations_ignores_a_single_scattered_bad_locate():
    """One bad locate must not poison everything that comes after it — the
    naive "running max" approach this replaced would flag all four points
    after the outlier, not just the outlier itself."""
    run, flags = sync_map_audit._book_order_violations([10, 20, 999, 40, 50, 60])
    assert run == 1
    assert flags == [False, False, True, False, False, False]


def test_book_order_violations_finds_a_genuinely_reordered_block():
    """Two blocks swapped (the real #586/#595 audio-reordering shape): one of
    the two blocks is excluded from the longest increasing run."""
    # Book order 0..9; audio narrates 0-2, then 6-8 (swapped forward), then
    # 3-5 (swapped back), then 9.
    positions = [0, 1, 2, 60, 70, 80, 30, 40, 50, 90]
    run, flags = sync_map_audit._book_order_violations(positions)
    assert run == 3
    # Exactly one of the two swapped 3-long blocks is excluded, not both.
    assert sum(flags) == 3


def test_book_order_violations_empty_input():
    assert sync_map_audit._book_order_violations([]) == (0, [])


SENTENCES = [_sentence(i) for i in range(20)]

# 3 minutes apart, in true speaking order — the "ground truth" the cached
# transcript records.
TRUE_START_MS = [i * 180_000 for i in range(20)]


def _transcript_sentences():
    return [
        {"text": SENTENCES[i], "start_ms": TRUE_START_MS[i], "end_ms": TRUE_START_MS[i] + 3000}
        for i in range(len(SENTENCES))
    ]


async def _seed_pair(db, tmp_path):
    """An EBook backed by a real EPUB containing every sentence's text (so the
    hash + text-sample checks are healthy on their own), paired with an
    audiobook."""
    body = "".join(f"<p>{s}.</p>" for s in SENTENCES)
    path = write_epub(
        tmp_path / "book.epub",
        [("c1.xhtml", f"<html><body>{body}</body></html>")],
    )
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
    return pair, path


async def _add_transcript(db, pair_id):
    db.add(AudioTranscript(
        pair_id=pair_id, audiobook_path="/x/a.m4b",
        sentence_count=len(SENTENCES),
        sentences_json=json.dumps(_transcript_sentences()),
    ))
    await db.commit()


def _healthy_points():
    return [(0, i, TRUE_START_MS[i], SENTENCES[i]) for i in range(len(SENTENCES))]


def _displaced_block_points():
    """Sentences 5-9 (a contiguous run, 25% of the map) carry a sync-point
    timestamp 30 minutes off from where the cached transcript actually has
    that text. The transcript itself (`_transcript_sentences()`, unchanged by
    this helper) is still fully in book order — only the *map's* stored
    timestamp for this block is wrong. Originally written to model issue
    #586's swapped-audio scenario, but re-checking a real flagged pair for
    issue #595 found the opposite: the audio was fine and only the map was
    displaced. `_out_of_order_transcript_sentences` below is the audio-really-
    reordered case; this one is the map-displaced case."""
    points = []
    for i in range(len(SENTENCES)):
        ms = TRUE_START_MS[i]
        if 5 <= i < 10:
            ms = TRUE_START_MS[i + 10]
        points.append((0, i, ms, SENTENCES[i]))
    return points


async def test_timing_check_flags_a_map_displaced_but_in_order_transcript(db, tmp_path):
    """Issue #595: when the cached transcript's own text is in book order,
    the audio is fine and the map itself is what's displaced — re-align from
    the transcript rather than pointing at the audio file."""
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(db, pair.id, _displaced_block_points(),
                         epub_file_hash=hash_file(path))
    await _add_transcript(db, pair.id)  # unmodified: fully in book order

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "match"
    assert row["timing_status"] == "flagged"
    assert row["timing_checked"] == 20
    assert row["timing_mismatches"] == 5
    assert row["timing_mismatch_rate"] == 0.25
    assert row["timing_mismatch_run"] == 5  # indices 5-9, contiguous
    assert row["timing_mismatch_chapters"] == (0, 0)  # all in chapter 0
    assert row["timing_transcript_confirmed_in_order"] is True
    assert row["status"] == "degraded"
    assert row["suggested_action"] == "realign"
    assert row["realign_path"] == f"/api/transcription/{pair.id}/realign"
    assert "in book order" in row["reason"]
    assert "re-align" in row["reason"].lower()


def _out_of_order_transcript_sentences():
    """The audio's own narration genuinely swaps two blocks: sentences 5-9
    are actually spoken where 10-14 belong, and vice versa — unlike
    `_displaced_block_points` above, this is the cached transcript's own
    *content* out of order, not just the map's stored timestamp. `start_ms`
    stays strictly increasing (it's the audio's real timeline; transcription
    is inherently sequential) — what moves is which sentence's text sits at
    which position."""
    reading_order = list(range(len(SENTENCES)))
    reading_order[5:10], reading_order[10:15] = reading_order[10:15], reading_order[5:10]
    return [
        {"text": SENTENCES[reading_order[pos]], "start_ms": pos * 180_000,
         "end_ms": pos * 180_000 + 3000}
        for pos in range(len(SENTENCES))
    ]


async def test_timing_check_flags_a_genuinely_reordered_transcript(db, tmp_path):
    """Issue #595: when the cached transcript's own text is NOT in book
    order, the audio really is the problem — keep pointing at it, not at
    re-aligning (which would just rebuild from the same reordered audio)."""
    pair, path = await _seed_pair(db, tmp_path)
    # The map itself claims healthy, book-order timestamps; it's the
    # transcript's own content that disagrees with book order.
    await make_sync_map(db, pair.id, _healthy_points(), epub_file_hash=hash_file(path))
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=len(SENTENCES),
        sentences_json=json.dumps(_out_of_order_transcript_sentences()),
    ))
    await db.commit()

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["timing_status"] == "flagged"
    assert row["timing_transcript_confirmed_in_order"] is False
    assert row["status"] == "degraded"
    assert row["suggested_action"] == "check_audio_order"
    assert row["realign_path"] is None
    assert "audio's order differs" in row["reason"]


async def test_timing_check_passes_a_healthy_map(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(db, pair.id, _healthy_points(), epub_file_hash=hash_file(path))
    await _add_transcript(db, pair.id)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["timing_status"] == "ok"
    assert row["timing_mismatches"] == 0
    assert row["status"] == "healthy"
    assert row["degraded"] is False
    assert row["suggested_action"] is None


async def test_a_few_scattered_mismatches_do_not_flag_the_pair(db, tmp_path):
    """Guard against false positives: an isolated stray offset (a single
    mis-transcribed sentence, say) must not push a healthy map over the
    mismatch-share threshold."""
    pair, path = await _seed_pair(db, tmp_path)
    points = _healthy_points()
    # One point out of 20 (5%) is off by more than the threshold — below
    # AUDIT_TIMING_MISMATCH_FRACTION (10%).
    ch, si, _ms, preview = points[3]
    points[3] = (ch, si, TRUE_START_MS[3] + 10 * 60_000, preview)
    await make_sync_map(db, pair.id, points, epub_file_hash=hash_file(path))
    await _add_transcript(db, pair.id)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["timing_mismatches"] == 1
    assert row["timing_mismatch_run"] == 1
    assert row["timing_status"] == "ok"
    assert row["status"] == "healthy"


async def test_scattered_mismatches_above_the_fraction_but_not_contiguous_do_not_flag(db, tmp_path):
    """The fraction alone is not enough to flag (issue #586 code review): a
    reordered block is *localized*, so the check requires a contiguous run of
    mismatched samples, not just an elevated share. 5 of 20 points (25%,
    clearing the fraction threshold) are each individually off, but spread one
    every four points — never three in a row — the opposite of what a swapped
    block of audio actually looks like."""
    pair, path = await _seed_pair(db, tmp_path)
    points = _healthy_points()
    for i in (2, 6, 10, 14, 18):
        ch, si, _ms, preview = points[i]
        points[i] = (ch, si, TRUE_START_MS[i] + 10 * 60_000, preview)
    await make_sync_map(db, pair.id, points, epub_file_hash=hash_file(path))
    await _add_transcript(db, pair.id)

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["timing_mismatches"] == 5
    assert row["timing_mismatch_rate"] == 0.25
    assert row["timing_mismatch_run"] == 1  # never two, let alone three, in a row
    assert row["timing_status"] == "ok"
    assert row["status"] == "healthy"


async def test_near_duplicate_sentences_are_not_falsely_flagged(db, tmp_path):
    """A synthetic healthy map where every sentence shares the same template
    and differs only by a number (near-duplicate from a fuzzy-matching
    standpoint: `token_set_ratio` scores ~99 between any two of them) must not
    be flagged. The ambiguity guard (`TIMING_MATCH_AMBIGUITY_MARGIN`) is what
    keeps this safe — it refuses to trust a "best" match that barely beats the
    runner-up, so these points come back unlocatable (cannot_check / a low
    `timing_checked`), never wrongly located and counted as mismatched."""
    near_dup_sentences = [
        f"chapter three opens with a quiet room number {i} and a long hallway beyond"
        for i in range(20)
    ]
    body = "".join(f"<p>{s}.</p>" for s in near_dup_sentences)
    path = write_epub(
        tmp_path / "book.epub",
        [("c1.xhtml", f"<html><body>{body}</body></html>")],
    )
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

    points = [(0, i, TRUE_START_MS[i], near_dup_sentences[i]) for i in range(20)]
    await make_sync_map(db, pair.id, points, epub_file_hash=hash_file(path))
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=20,
        sentences_json=json.dumps([
            {"text": near_dup_sentences[i], "start_ms": TRUE_START_MS[i],
             "end_ms": TRUE_START_MS[i] + 3000}
            for i in range(20)
        ]),
    ))
    await db.commit()

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["timing_status"] != "flagged"
    assert row["status"] != "degraded"


async def test_missing_transcript_is_reported_as_cannot_check_not_a_failure(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(db, pair.id, _healthy_points(), epub_file_hash=hash_file(path))
    # No AudioTranscript row for this pair.

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["has_cached_transcript"] is False
    assert row["timing_status"] == "cannot_check"
    assert row["timing_checked"] == 0
    assert row["timing_mismatch_rate"] is None
    assert row["status"] == "healthy"


async def test_timing_check_is_skipped_when_sampling_is_off(db, tmp_path):
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(db, pair.id, _displaced_block_points(),
                         epub_file_hash=hash_file(path))
    await _add_transcript(db, pair.id)

    [row] = await sync_map_audit.audit_sync_maps(db, sample_size=0)

    assert row["timing_status"] == "skipped"
    # sample_size=0 is a fast provenance-only pass — the hash alone still
    # reports healthy, degraded classification included, since nothing was
    # sampled either for text or for timing.
    assert row["status"] == "healthy"


async def test_degraded_flag_from_alignment_time_is_surfaced(db, tmp_path):
    """`sync_maps.degraded`, stamped by `sync_engine.save_sync_map` from
    `alignment.AlignmentDiagnostics` — independent of the timing check, which
    has nothing to compare against here (no cached transcript)."""
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(
        db, pair.id, _healthy_points(), epub_file_hash=hash_file(path),
        degraded=True,
        degraded_reason="18/60 raw anchors (30%) rejected, displaced run of 12.",
    )

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["degraded"] is True
    assert row["status"] == "degraded"
    assert row["suggested_action"] == "check_audio_order"
    assert "18/60 raw anchors" in row["reason"]


async def test_clean_timing_check_overrides_the_alignment_time_degraded_flag(db, tmp_path):
    """Issue #620: a tiny anchor pool can trip the alignment-time `degraded`
    flag even when the resulting map is fine (`alignment.py`'s own
    `MIN_KEPT_ANCHORS_FOR_DEGRADED` softens this but doesn't guarantee it
    never happens). The audit's own timing check is a direct, sampled
    comparison against the cached transcript — when it runs a well-sampled
    check and finds nothing wrong, that overrides the alignment-time
    heuristic instead of compounding with it."""
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(
        db, pair.id, _healthy_points(), epub_file_hash=hash_file(path),
        degraded=True,
        degraded_reason="6/8 raw anchors (75%) rejected ..., run of 6 displaced ~581 min.",
    )
    await _add_transcript(db, pair.id)  # in book order, clean

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["timing_status"] == "ok"
    assert row["timing_checked"] >= 20
    # The raw alignment-time flag is still surfaced for an operator to see...
    assert row["degraded"] is True
    assert row["degraded_reason"]
    # ...but it no longer promotes the verdict, given the clean timing check.
    assert row["status"] == "healthy"
    assert row["suggested_action"] is None


async def test_degraded_flag_does_not_override_a_stale_verdict(db, tmp_path):
    """A wrong-file problem (already `stale`) is the bigger issue; the
    degraded flag is still reported, but doesn't relabel the verdict or its
    suggested action away from fixing the file mismatch first."""
    pair, _path = await _seed_pair(db, tmp_path)
    await make_sync_map(
        db, pair.id, _healthy_points(), epub_file_hash="0" * 64,  # mismatch
        degraded=True, degraded_reason="displaced block detected",
    )

    [row] = await sync_map_audit.audit_sync_maps(db)

    assert row["hash_status"] == "mismatch"
    assert row["status"] == "stale"
    assert row["degraded"] is True


async def test_endpoint_counts_degraded_pairs_as_flagged(
    db, tmp_path, make_client, make_user, auth_header
):
    from routers import troubleshoot
    editor = await make_user(role="editor")
    pair, path = await _seed_pair(db, tmp_path)
    await make_sync_map(db, pair.id, _displaced_block_points(),
                         epub_file_hash=hash_file(path))
    await _add_transcript(db, pair.id)

    async with make_client(troubleshoot.router) as c:
        r = await c.get("/api/troubleshoot/sync-map-audit",
                        headers=auth_header(editor))

    assert r.status_code == 200
    body = r.json()
    assert body["flagged"] == 1
    [row] = body["pairs"]
    assert row["status"] == "degraded"
    assert row["timing_status"] == "flagged"
