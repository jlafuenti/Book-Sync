"""
Alignment carries per-token start times from the transcript's word timing onto
matched points, and the sync-map save writes them to `sync_points.word_starts`
(issue #835).
"""

from sqlalchemy import select

from models.sync_map import SyncPoint
from services.alignment import AlignedPoint, _repair_outliers, align_texts
from services.sync_engine import save_sync_map
from tests.factories import make_book_pair
from tests.test_alignment import EpubSentence, TranscribedSentence

SENTENCES = [
    "alpha bravo charlie delta echo",
    "foxtrot golf hotel india juliet",
    "kilo lima mike november oscar",
    "papa quebec romeo sierra tango",
]


def _whisper(with_words_for=(0, 1, 2, 3), skip_text_for=()):
    out = []
    for i, text in enumerate(SENTENCES):
        start = i * 5000
        words = []
        if i in with_words_for:
            words = [
                {"text": tok, "start_ms": start + k * 400, "end_ms": start + k * 400 + 380}
                for k, tok in enumerate(text.split())
            ]
        out.append(TranscribedSentence(
            text="completely unrelated words here" if i in skip_text_for else text,
            start_ms=start, end_ms=start + 4000, words=words,
        ))
    return out


def _epub():
    return [EpubSentence(chapter=0, sentence_index=i, text=t) for i, t in enumerate(SENTENCES)]


def test_matched_points_carry_word_starts():
    points = align_texts(_epub(), _whisper())
    assert all(p.confidence > 0 for p in points)
    assert points[1].word_starts == [5000, 5400, 5800, 6200, 6600]


def test_a_matched_point_without_words_has_none():
    points = align_texts(_epub(), _whisper(with_words_for=(0, 2, 3)))
    assert points[1].word_starts is None
    assert points[0].word_starts is not None


def test_an_interpolated_point_has_none():
    whisper = _whisper()
    del whisper[1]  # the audio never says the second sentence
    points = align_texts(_epub(), whisper)
    assert points[1].confidence == 0.0
    assert points[1].word_starts is None
    assert points[0].word_starts is not None


def test_a_demoted_outlier_loses_its_word_starts():
    pts = [
        AlignedPoint(0, i, "t", ms, ms + 1000, 1.0, word_starts=[ms, ms + 100])
        for i, ms in enumerate([0, 1000, 2000, 900_000, 4000, 5000])
    ]
    out = _repair_outliers(pts)
    assert out[3].confidence == 0.0
    assert out[3].word_starts is None
    assert out[2].word_starts == [2000, 2100]


def test_a_point_demoted_for_going_backwards_loses_its_word_starts():
    pts = [
        AlignedPoint(0, 0, "t", 5000, 6000, 1.0, word_starts=[5000]),
        AlignedPoint(0, 1, "t", 1000, 2000, 1.0, word_starts=[1000]),
    ]
    out = _repair_outliers(pts)
    assert out[1].confidence == 0.0
    assert out[1].word_starts is None


async def test_word_starts_round_trip_through_the_saved_map(db):
    pair = await make_book_pair(db)
    points = [
        AlignedPoint(0, 0, "a b c", 0, 900, 1.0, word_starts=[0, 300, 600]),
        AlignedPoint(0, 1, "d e", 1000, 1900, 0.0),
    ]
    await save_sync_map(db, book_pair_id=pair.id, aligned_points=points)
    await db.commit()

    rows = (await db.execute(
        select(SyncPoint).order_by(SyncPoint.epub_sentence_index)
    )).scalars().all()
    assert rows[0].word_starts == "0,300,600"
    assert rows[1].word_starts is None
