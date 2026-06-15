"""
Alignment tests: synthetic books reproducing the drift the old chunked DTW
suffered (minutes-off sync after a region of inserted/garbled narration).

Uses local stand-in dataclasses (same fields as EpubSentence /
TranscribedSentence) so the tests run without torch/whisper/ebooklib
installed — alignment.py is duck-typed and import-light.
"""
import os
import random
import sys
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.alignment import align_texts


@dataclass
class EpubSentence:
    chapter: int
    sentence_index: int
    text: str
    chapter_title: str = ""


@dataclass
class TranscribedSentence:
    text: str
    start_ms: int
    end_ms: int
    words: List[dict] = field(default_factory=list)


def _make_clean_book(num=1000, garbage_at=300, garbage_len=40):
    """Distinct sentences, one-to-one with audio, plus a short garbage run."""
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima", "mike", "november"]
    epub, whisper = [], []
    t = 0
    for i in range(num):
        text = " ".join(words[(i + j) % len(words)] + str(i * 7 + j) for j in range(12))
        epub.append(EpubSentence(chapter=i // 50, sentence_index=i % 50, text=text))
        if garbage_at <= i < garbage_at + garbage_len:
            whisper.append(TranscribedSentence(text="la la la humming noise", start_ms=t, end_ms=t + 3000))
        else:
            whisper.append(TranscribedSentence(text=text, start_ms=t, end_ms=t + 3000))
        t += 3000
    return epub, whisper


def _make_realistic_book():
    """
    The scenario that made the old chunked DTW drift by minutes:
    - limited vocabulary (many similar sentences, like real prose)
    - ~14% word-level transcription noise (drops + mishears)
    - 40-sentence narrator intro before the book starts
    - 150 sentences of extra/hallucinated narration inserted mid-book

    Returns (epub, whisper, truth) where truth[i] is the true start_ms of
    epub sentence i in the audio.
    """
    vocab = ("the a he she they said walked door room dark light slowly "
             "quickly turned looked saw heard old young man woman house "
             "street night day hand eyes face voice").split()
    rng = random.Random(7)

    def sentence():
        return " ".join(rng.choice(vocab) for _ in range(rng.randint(6, 18)))

    def noisy(s):
        out = []
        for w in s.split():
            r = rng.random()
            if r < 0.08:
                continue                      # dropped word
            elif r < 0.14:
                out.append(rng.choice(vocab))  # misheard word
            else:
                out.append(w)
        return " ".join(out)

    epub_texts = [sentence() for _ in range(1000)]
    epub = [EpubSentence(chapter=i // 50, sentence_index=i % 50, text=epub_texts[i])
            for i in range(1000)]

    whisper, truth = [], {}
    t = 0

    def emit(txt, dur=3000):
        nonlocal t
        whisper.append(TranscribedSentence(text=txt, start_ms=t, end_ms=t + dur))
        t += dur

    for k in range(40):
        emit(f"audible presents narrated by famous person {k}")
    for i in range(1000):
        if i == 350:
            for _ in range(150):
                emit(noisy(sentence()))       # extra narration not in the epub
        truth[i] = t
        emit(noisy(epub_texts[i]))
    return epub, whisper, truth


def test_no_drift_after_garbage_region():
    epub, whisper = _make_clean_book()
    points = align_texts(epub, whisper)
    assert len(points) == len(epub)
    # Sentence i is spoken at exactly i*3000 ms.
    for i in (500, 700, 900, 999):
        expected = i * 3000
        assert abs(points[i].audio_start_ms - expected) < 30_000, (
            f"sentence {i}: got {points[i].audio_start_ms}, expected ~{expected}"
        )


def test_no_drift_with_insertion_and_noise():
    """The old chunked DTW was 100-280 s off after the inserted region;
    anchor-based alignment must stay within 30 s everywhere."""
    epub, whisper, truth = _make_realistic_book()
    points = align_texts(epub, whisper)
    assert len(points) == len(epub)
    for i in (100, 400, 600, 800, 999):
        assert abs(points[i].audio_start_ms - truth[i]) < 30_000, (
            f"sentence {i}: got {points[i].audio_start_ms}, expected ~{truth[i]}"
        )


def test_monotonic_audio_times():
    epub, whisper, _ = _make_realistic_book()
    points = align_texts(epub, whisper)
    last = -1
    for p in points:
        assert p.audio_start_ms >= last
        last = p.audio_start_ms


def test_confidence_present_on_matches():
    epub, whisper = _make_clean_book(num=200, garbage_len=0)
    points = align_texts(epub, whisper)
    matched = [p for p in points if p.confidence > 0]
    assert len(matched) > len(points) * 0.8
