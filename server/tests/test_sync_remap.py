"""
Re-transcription must not leave bookmarks pointing at the wrong sentence
(issue #55).

`save_sync_map` deletes the old `SyncMap` and inserts a fresh set of points with
a bumped version. `bookmarks.epub_sentence_index` is a *sync-map coordinate*, so
re-segmentation silently re-points it at different text — and Android turns that
coordinate back into preview text (`epubTextForSentence`) to restore the reader's
page. These tests pin the translation: the same place in the book, expressed in
the new map's coordinates.

The load-bearing rules:

- an **audiobook**-sourced bookmark is re-derived from `audio_position_ms`; the
  audio file didn't change, so that field is the truth and stays untouched;
- an **ebook**-sourced bookmark is re-derived by matching its text anchor with
  the shared matcher, and its *derived* `audio_position_ms` is refreshed;
- `anchor_revision` bumps **only** when the chapter moves — a sentence-index-only
  shift is the same page, so device hints stay current;
- `captured_at` is never touched: a remap is a server-side translation, not a
  device capture, and must not win a staleness comparison against a real write.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.book import BookPair
from models.bookmark import (
    Bookmark, BookmarkLog, BookmarkSource, HintKind, PositionHint,
)
from models.progress import ProgressType, UserProgress
from schemas import BookPairResponse
from services.alignment import AlignedPoint
from services.sync_engine import save_sync_map
from utils import utcnow

from tests.factories import ensure_users, make_book_pair


# The "old" map: two chapters, one sentence each per chapter plus a filler.
OLD_POINTS = [
    ("the quick brown fox jumps over the lazy dog", 0, 0, 0),
    ("pack my box with five dozen liquor jugs", 0, 1, 5_000),
    ("how vexingly quick daft zebras jump", 1, 0, 10_000),
    ("sphinx of black quartz judge my vow", 1, 1, 15_000),
]

# The "new" map after a re-transcription: the same sentences, but two extra ones
# have appeared ahead of them, so every index in chapter 0 shifts by two and
# chapter 1 by one. Timestamps move slightly too (a better model).
NEW_POINTS = [
    ("a wizard job is to vex chumps quickly in fog", 0, 0, 0),
    ("jackdaws love my big sphinx of quartz", 0, 1, 1_500),
    ("the quick brown fox jumps over the lazy dog", 0, 2, 3_000),
    ("pack my box with five dozen liquor jugs", 0, 3, 6_500),
    ("bright vixens jump dozy fowl quack", 1, 0, 9_000),
    ("how vexingly quick daft zebras jump", 1, 1, 11_000),
    ("sphinx of black quartz judge my vow", 1, 2, 16_000),
]


@pytest.fixture(autouse=True)
async def _readers(db):
    """The hard-coded `user_id=1`/`2` below need real rows now that the harness
    enforces foreign keys (issue #198)."""
    await ensure_users(db, 1, 2)


def _aligned(points):
    return [
        AlignedPoint(
            epub_chapter=ch, epub_sentence_index=si, epub_text_preview=text,
            audio_start_ms=ms, audio_end_ms=ms + 1_000, confidence=1.0,
        )
        for text, ch, si, ms in points
    ]


async def _seed(db, *, source, epub_chapter, epub_sentence_index,
                epub_text_preview, audio_position_ms, user_id=1,
                with_hint=False):
    """A pair carrying the OLD map plus one bookmark on it."""
    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await db.commit()

    bookmark = Bookmark(
        user_id=user_id,
        book_pair_id=pair.id,
        source=source,
        epub_chapter=epub_chapter,
        epub_sentence_index=epub_sentence_index,
        epub_text_preview=epub_text_preview,
        epub_progress_percent=42.0,
        audio_position_ms=audio_position_ms,
        anchor_revision=7,
        captured_at=utcnow(),
        sync_map_version=1,
    )
    db.add(bookmark)
    await db.flush()
    if with_hint:
        db.add(PositionHint(
            bookmark_id=bookmark.id, device_id="phone",
            hint_kind=HintKind.READIUM_LOCATOR, hint_value="{}",
            anchor_revision=7, audio_position_ms=audio_position_ms,
        ))
    await db.commit()
    return pair, bookmark


async def _retranscribe(db, pair_id, points=None):
    sm = await save_sync_map(db, pair_id, _aligned(NEW_POINTS if points is None else points))
    await db.commit()
    return sm


# ---------------------------------------------------------------------------
# ebook-sourced: text anchor drives the remap
# ---------------------------------------------------------------------------

async def test_ebook_bookmark_sentence_index_follows_its_text(db):
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000, with_hint=True,
    )

    sm = await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    # Same sentence, new index (1 -> 3) and new timestamp (5000 -> 6500).
    assert bookmark.epub_chapter == 0
    assert bookmark.epub_sentence_index == 3
    assert bookmark.audio_position_ms == 6_500
    assert bookmark.sync_map_version == sm.version == 2
    # The page didn't move, so the device's locator is still current.
    assert bookmark.anchor_revision == 7


async def test_ebook_remap_leaves_the_devices_hint_current(db):
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=1, epub_sentence_index=0,
        epub_text_preview="how vexingly quick daft zebras jump",
        audio_position_ms=10_000, with_hint=True,
    )

    await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    hint = (await db.execute(
        select(PositionHint).where(PositionHint.bookmark_id == bookmark.id)
    )).scalar_one()
    assert bookmark.epub_sentence_index == 1
    assert hint.anchor_revision == bookmark.anchor_revision


async def test_remap_across_chapters_bumps_anchor_revision(db):
    """A relocation to another spine item genuinely moves the page."""
    # The bookmark's text now lives in chapter 1 of the new map.
    moved = [
        ("filler sentence one for chapter zero", 0, 0, 0),
        ("pack my box with five dozen liquor jugs", 1, 4, 9_000),
    ]
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000, with_hint=True,
    )

    await _retranscribe(db, pair.id, points=moved)

    await db.refresh(bookmark)
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (1, 4)
    assert bookmark.anchor_revision == 8
    hint = (await db.execute(
        select(PositionHint).where(PositionHint.bookmark_id == bookmark.id)
    )).scalar_one()
    assert hint.anchor_revision != bookmark.anchor_revision


async def test_ebook_bookmark_without_preview_uses_the_old_points_text(db):
    """The old map is read before it is deleted, so a preview-less bookmark
    still has a text anchor to translate."""
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=1, epub_sentence_index=1,
        epub_text_preview=None,
        audio_position_ms=None,
    )

    await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    # Old (1, 1) was "sphinx of black quartz judge my vow" -> new (1, 2).
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (1, 2)


# ---------------------------------------------------------------------------
# audiobook-sourced: the audio position is the truth
# ---------------------------------------------------------------------------

async def test_audiobook_bookmark_recomputes_epub_side_from_audio(db):
    pair, bookmark = await _seed(
        db, source=BookmarkSource.AUDIOBOOK,
        epub_chapter=1, epub_sentence_index=0,
        epub_text_preview="how vexingly quick daft zebras jump",
        audio_position_ms=12_000,
    )

    await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    # 12_000ms falls inside the new (1, 1) point which starts at 11_000.
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (1, 1)
    assert bookmark.audio_position_ms == 12_000  # never rewritten


# ---------------------------------------------------------------------------
# The cases that must change nothing
# ---------------------------------------------------------------------------

async def test_first_ever_sync_map_leaves_bookmarks_alone(db):
    """No prior map means no old coordinates to translate from."""
    pair = await make_book_pair(db)
    bookmark = Bookmark(
        user_id=1, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000, anchor_revision=7,
    )
    db.add(bookmark)
    await db.commit()

    await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    assert bookmark.epub_sentence_index == 1
    assert bookmark.anchor_revision == 7


async def test_unmatchable_bookmark_is_left_untouched_and_stays_flagged(db):
    """No text anchor and no audio position: nothing to translate from. The
    coordinates stand and `sync_map_version` stays stale, which is how the
    drift remains detectable."""
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=9, epub_sentence_index=4,   # no such point in the old map
        epub_text_preview=None,
        audio_position_ms=None,
    )

    await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (9, 4)
    assert bookmark.sync_map_version == 1
    assert bookmark.anchor_revision == 7


async def test_bookmarks_on_other_pairs_are_untouched(db):
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000,
    )
    other = await make_book_pair(db, ebook_title="Other", audiobook_title="Other")
    await save_sync_map(db, other.id, _aligned(OLD_POINTS))
    stranger = Bookmark(
        user_id=1, book_pair_id=other.id, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000,
    )
    db.add(stranger)
    await db.commit()

    await _retranscribe(db, pair.id)

    await db.refresh(stranger)
    assert stranger.epub_sentence_index == 1


async def test_every_users_bookmark_on_the_pair_is_remapped(db):
    pair, first = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000, user_id=1,
    )
    second = Bookmark(
        user_id=2, book_pair_id=pair.id, source=BookmarkSource.EBOOK,
        epub_chapter=1, epub_sentence_index=0,
        epub_text_preview="how vexingly quick daft zebras jump",
        audio_position_ms=10_000,
    )
    db.add(second)
    await db.commit()

    await _retranscribe(db, pair.id)

    await db.refresh(first)
    await db.refresh(second)
    assert first.epub_sentence_index == 3
    assert (second.epub_chapter, second.epub_sentence_index) == (1, 1)


# ---------------------------------------------------------------------------
# Interaction with the rest of the position contract
# ---------------------------------------------------------------------------

async def test_remap_does_not_touch_captured_at_or_write_a_log_row(db):
    """A remap is a translation, not a device capture. Stamping `captured_at`
    would let it beat a genuinely newer write from a phone, and a `BookmarkLog`
    row would put a move the user never made into Session History."""
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000,
    )
    before = bookmark.captured_at

    await _retranscribe(db, pair.id)

    await db.refresh(bookmark)
    assert bookmark.captured_at == before
    logs = (await db.execute(
        select(BookmarkLog).where(BookmarkLog.bookmark_id == bookmark.id)
    )).scalars().all()
    assert logs == []


async def _pair_at_v2(db):
    """A pair whose map has been regenerated once: OLD_POINTS was v1, NEW_POINTS is v2."""
    from schemas import PositionScope
    from services.position_service import ScopeRef

    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await _retranscribe(db, pair.id)  # now at version 2
    ref = ScopeRef(PositionScope.PAIR, book_pair_id=pair.id,
                   ebook_id=pair.ebook_id, audiobook_id=pair.audiobook_id)
    return pair, ref


async def test_apply_position_stamps_the_version_the_client_attests_to(db):
    """A write carrying the live version is recorded against it — the ordinary
    case, and the one the column exists to track (issue #116)."""
    from schemas import PositionUpdate
    from services.position_service import apply_position

    pair, ref = await _pair_at_v2(db)
    record, accepted = await apply_position(db, 1, ref, PositionUpdate(
        epub_chapter=0, epub_sentence_index=3, sync_map_version=2,
        epub_text_preview="pack my box with five dozen liquor jugs",
    ))

    assert accepted
    assert (record.epub_chapter, record.epub_sentence_index) == (0, 3)
    assert record.sync_map_version == 2


async def test_apply_position_without_an_attested_version_stamps_null(db):
    """A client that doesn't say which map its index came from gets NULL —
    "unknown" — rather than the current version. Claiming currency for a
    coordinate nobody vouched for is exactly the false claim #116 describes."""
    from schemas import PositionUpdate
    from services.position_service import apply_position

    pair, ref = await _pair_at_v2(db)
    record, accepted = await apply_position(db, 1, ref, PositionUpdate(
        epub_chapter=0, epub_sentence_index=3,
        epub_text_preview="pack my box with five dozen liquor jugs",
    ))

    assert accepted
    assert record.sync_map_version is None


async def test_stale_ebook_write_with_a_usable_preview_is_re_anchored(db):
    """The phone pushes v1 coordinates (0, 1) after the map moved to v2. Its
    preview names the same sentence, so the write lands re-expressed in v2
    terms — (0, 3), audio 6500 — and is stamped current, instead of the stale
    index being recorded as if it were current."""
    from schemas import PositionUpdate
    from services.position_service import apply_position

    pair, ref = await _pair_at_v2(db)
    record, accepted = await apply_position(db, 1, ref, PositionUpdate(
        source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1, sync_map_version=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000,
    ))

    assert accepted
    assert (record.epub_chapter, record.epub_sentence_index) == (0, 3)
    assert record.audio_position_ms == 6_500
    assert record.sync_map_version == 2


async def test_stale_audiobook_write_is_re_derived_from_its_audio_position(db):
    """An audiobook-sourced write's audio position is the truth; its epub side
    was derived from the old map, so it is re-derived from the new one."""
    from schemas import PositionUpdate
    from services.position_service import apply_position

    pair, ref = await _pair_at_v2(db)
    record, accepted = await apply_position(db, 1, ref, PositionUpdate(
        source=BookmarkSource.AUDIOBOOK,
        epub_chapter=1, epub_sentence_index=0, sync_map_version=1,
        audio_position_ms=11_500,
    ))

    assert accepted
    # 11 500 ms in the v2 map is "how vexingly quick daft zebras jump" = (1, 1).
    assert (record.epub_chapter, record.epub_sentence_index) == (1, 1)
    assert record.audio_position_ms == 11_500
    assert record.sync_map_version == 2


async def test_stale_write_without_a_usable_anchor_keeps_its_stale_version(db):
    """Nothing to re-anchor from: the coordinates are kept as sent, and the row
    keeps saying v1 so the drift stays detectable."""
    from schemas import PositionUpdate
    from services.position_service import apply_position

    pair, ref = await _pair_at_v2(db)
    record, accepted = await apply_position(db, 1, ref, PositionUpdate(
        source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1, sync_map_version=1,
        epub_text_preview="zzz",  # too short to match anything
    ))

    assert accepted
    assert (record.epub_chapter, record.epub_sentence_index) == (0, 1)
    assert record.sync_map_version == 1


async def test_position_response_reports_the_sync_map_version(db):
    """Clients pulling a position learn what its coordinates are expressed in,
    so a later deferred push can attest the right version."""
    from schemas import PositionResponse, PositionUpdate
    from services.position_service import apply_position, to_response_dict

    pair, ref = await _pair_at_v2(db)
    record, _ = await apply_position(db, 1, ref, PositionUpdate(
        epub_chapter=0, epub_sentence_index=3, sync_map_version=2,
    ))

    payload = PositionResponse.model_validate(to_response_dict(record, ref))
    assert payload.sync_map_version == 2


async def test_match_text_reports_the_sync_map_version(db, make_client, make_user, auth_header):
    """The web resolves its sentence index server-side via /match-text, so that
    response is where it learns which version to attest to."""
    from routers import sync as sync_router

    pair, _ = await _pair_at_v2(db)
    user = await make_user()

    async with make_client(sync_router.router) as c:
        resp = await c.post(
            f"/api/sync/match-text/{pair.id}", headers=auth_header(user),
            json={"epub_text": "pack my box with five dozen liquor jugs", "chapter_hint": 0},
        )

    assert resp.status_code == 200
    assert resp.json()["sync_map_version"] == 2
    assert resp.json()["epub_sentence_index"] == 3


async def test_apply_position_without_a_sentence_index_leaves_the_version_alone(db):
    """A completion toggle or an audio-only heartbeat establishes no sync-map
    coordinate, so it must not claim one."""
    from schemas import PositionScope, PositionUpdate
    from services.position_service import ScopeRef, apply_position

    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await db.commit()

    ref = ScopeRef(PositionScope.PAIR, book_pair_id=pair.id,
                   ebook_id=pair.ebook_id, audiobook_id=pair.audiobook_id)
    record, _ = await apply_position(db, 1, ref, PositionUpdate(audio_position_ms=1_234))

    assert record.sync_map_version is None


# ---------------------------------------------------------------------------
# Exposing the version to clients
# ---------------------------------------------------------------------------

async def test_pairs_listing_reports_the_sync_map_version(db, make_client, make_user,
                                                          auth_header):
    """Clients cache sync points and need to notice a regeneration without
    downloading the whole map to find out (issue #55)."""
    from routers import library

    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await _retranscribe(db, pair.id)
    user = await make_user()

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/pairs", headers=auth_header(user))

    assert resp.status_code == 200
    body = {p["id"]: p for p in resp.json()["items"]}
    assert body[pair.id]["sync_map_version"] == 2


async def test_pair_without_a_sync_map_reports_null(db, make_client, make_user,
                                                    auth_header):
    from routers import library

    pair = await make_book_pair(db)
    user = await make_user()

    async with make_client(library.router) as c:
        resp = await c.get("/api/library/pairs", headers=auth_header(user))

    assert resp.status_code == 200
    assert resp.json()["items"][0]["sync_map_version"] is None
    assert resp.json()["items"][0]["id"] == pair.id


async def test_serialising_a_pair_with_the_map_unloaded_does_not_lazy_load(db):
    """`sync_map` is a relationship, and an async lazy-load raises. Endpoints
    that don't eager-load it (POST /pairs, for one) must still serialise."""
    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await db.commit()
    db.expunge_all()

    unloaded = (await db.execute(
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .where(BookPair.id == pair.id)
    )).scalar_one()

    # Must not raise MissingGreenlet; "unknown" is reported as null.
    assert BookPairResponse.model_validate(unloaded).sync_map_version is None


async def test_chapter_change_is_projected_onto_user_progress(db):
    moved = [
        ("filler sentence one for chapter zero", 0, 0, 0),
        ("pack my box with five dozen liquor jugs", 1, 4, 9_000),
    ]
    pair, bookmark = await _seed(
        db, source=BookmarkSource.EBOOK,
        epub_chapter=0, epub_sentence_index=1,
        epub_text_preview="pack my box with five dozen liquor jugs",
        audio_position_ms=5_000,
    )
    db.add(UserProgress(
        user_id=1, media_type=ProgressType.EBOOK, ebook_id=pair.ebook_id,
        book_pair_id=pair.id, epub_chapter=0, epub_progress_percent=42.0,
    ))
    await db.commit()

    await _retranscribe(db, pair.id, points=moved)

    row = (await db.execute(
        select(UserProgress).where(
            UserProgress.user_id == 1,
            UserProgress.media_type == ProgressType.EBOOK,
            UserProgress.ebook_id == pair.ebook_id,
        )
    )).scalar_one()
    assert row.epub_chapter == 1


# ---------------------------------------------------------------------------
# Audio that precedes the new map (issue #200)
#
# `audio_to_epub` returned (0, 0) for any position earlier than every point,
# and `resolve_on_map` handed that back as a successful match — indistinguishable
# from a genuine hit on the first sentence. So a listener parked in unaligned
# front matter, or a prologue the aligner dropped, was moved to the start of the
# book, `anchor_revision` bumped (every device's hint stale at once) and
# `user_progress` rewritten. The contract says a record holding any anchor never
# resolves to start of book (docs/position-sync-contract.md).
#
# A map whose first point is at 9 s, so anything under that precedes it.
LATE_START_POINTS = [
    ("a wizard job is to vex chumps quickly in fog", 0, 0, 9_000),
    ("the quick brown fox jumps over the lazy dog", 0, 1, 12_000),
    ("how vexingly quick daft zebras jump", 1, 0, 20_000),
]


async def test_stale_audiobook_write_before_the_map_keeps_its_coordinates(db):
    """The same rule on the write path (issue #116 + #200). A version-mismatched
    audiobook write whose audio precedes every point on the live map keeps the
    coordinates it sent and stamps the *attested* version, so the row visibly
    trails — it must not be silently relocated to the start of the book."""
    from schemas import PositionUpdate
    from services.position_service import apply_position

    pair = await make_book_pair(db)
    await save_sync_map(db, pair.id, _aligned(OLD_POINTS))
    await _retranscribe(db, pair.id, points=LATE_START_POINTS)  # v2 starts at 9 s

    from schemas import PositionScope
    from services.position_service import ScopeRef
    ref = ScopeRef(PositionScope.PAIR, book_pair_id=pair.id,
                   ebook_id=pair.ebook_id, audiobook_id=pair.audiobook_id)

    record, accepted = await apply_position(db, 1, ref, PositionUpdate(
        source=BookmarkSource.AUDIOBOOK,
        epub_chapter=3, epub_sentence_index=2, sync_map_version=1,
        audio_position_ms=1_000,
    ))

    assert accepted
    assert (record.epub_chapter, record.epub_sentence_index) == (3, 2)
    assert record.audio_position_ms == 1_000
    assert record.sync_map_version == 1


async def test_audiobook_bookmark_before_the_first_point_is_left_untouched(db):
    """Nothing to translate from: no point covers the position and there is no
    usable text anchor. The coordinates stand and the stale version stays
    visible, exactly as an unmatchable ebook bookmark does."""
    pair, bookmark = await _seed(
        db, source=BookmarkSource.AUDIOBOOK,
        epub_chapter=3, epub_sentence_index=2,
        epub_text_preview=None,
        audio_position_ms=1_000,
        with_hint=True,
    )

    await _retranscribe(db, pair.id, points=LATE_START_POINTS)

    await db.refresh(bookmark)
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (3, 2)
    assert bookmark.audio_position_ms == 1_000
    assert bookmark.sync_map_version == 1, "the drift must stay visible"
    assert bookmark.anchor_revision == 7, "no chapter move, so no hint invalidation"

    hint = (await db.execute(
        select(PositionHint).where(PositionHint.bookmark_id == bookmark.id)
    )).scalar_one()
    assert hint.anchor_revision == bookmark.anchor_revision, "hint stays current"


async def test_audiobook_bookmark_before_the_first_point_falls_back_to_its_text(db):
    """A bookmark that *does* carry a preview is re-anchored by text rather
    than abandoned — better than either (0, 0) or giving up."""
    pair, bookmark = await _seed(
        db, source=BookmarkSource.AUDIOBOOK,
        epub_chapter=1, epub_sentence_index=0,
        epub_text_preview="how vexingly quick daft zebras jump",
        audio_position_ms=1_000,
    )

    sm = await _retranscribe(db, pair.id, points=LATE_START_POINTS)

    await db.refresh(bookmark)
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (1, 0)
    assert bookmark.sync_map_version == sm.version == 2
    # The text rung refreshes the derived audio position to the matched point.
    assert bookmark.audio_position_ms == 20_000


async def test_audiobook_bookmark_exactly_at_the_first_point_resolves_to_it(db):
    """The boundary is a hit, not a miss."""
    pair, bookmark = await _seed(
        db, source=BookmarkSource.AUDIOBOOK,
        epub_chapter=3, epub_sentence_index=2,
        epub_text_preview=None,
        audio_position_ms=9_000,
    )

    sm = await _retranscribe(db, pair.id, points=LATE_START_POINTS)

    await db.refresh(bookmark)
    assert (bookmark.epub_chapter, bookmark.epub_sentence_index) == (0, 0)
    assert bookmark.sync_map_version == sm.version == 2
    assert bookmark.audio_position_ms == 9_000, "the audio file did not change"
