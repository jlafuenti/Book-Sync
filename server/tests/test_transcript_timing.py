"""A cached transcript out of step with the audio file it is paired with.

The transcript cache is keyed to the audiobook's path, and a transcript written
before issue #588 has no fingerprint to compare against the file now on disk.
When the file is re-encoded in place, every timestamp can drift by a few
seconds while the book's total length moves too little for any duration check
to notice. A sync map aligned from that transcript is then a sentence or two
off everywhere: read-along highlights a sentence the narrator has not reached.

`services.transcript_timing` measures it directly. Sentence boundaries in a
correct transcript fall in the pauses of the real audio; this samples a few
windows of the file, finds the pauses with ffmpeg's `silencedetect`, and asks
which time shift puts the transcript's boundaries into them. All data here is
synthetic.
"""

import json
import random

import pytest

from services import transcript_timing as tt


# ---------------------------------------------------------------------------
# Synthetic audio: a pause after every sentence
# ---------------------------------------------------------------------------


def _speech(seed, total_s, *, min_len=1.0, max_len=6.0):
    """(sentence_starts, pauses) in absolute seconds over `total_s` of fake
    narration: each sentence runs 1-6 s and is followed by a 0.25-0.8 s pause,
    so the next sentence starts where that pause ends."""
    rng = random.Random(seed)
    starts, pauses = [], []
    t = 0.5
    while t < total_s:
        starts.append(t)
        end = t + rng.uniform(min_len, max_len)
        gap = rng.uniform(0.25, 0.8)
        pauses.append((end, end + gap))
        t = end + gap
    return starts, pauses


def _sentences_json(starts, offset_s):
    """A transcript whose every start is `offset_s` *earlier* than the real
    audio (positive offset = transcript early, the shape of the real case)."""
    rows = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else s + 2
        rows.append({
            "text": f"sentence {i}",
            "start_ms": int(round((s - offset_s) * 1000)),
            "end_ms": int(round((nxt - offset_s) * 1000)),
        })
    return json.dumps(rows)


def _detector(pauses):
    """A stand-in for the ffmpeg call: the pauses inside [start, start+length),
    relative to `start`, as `silencedetect` reports them for an input seek."""
    def detect(path, start_s, length_s):
        return [
            (a - start_s, b - start_s)
            for a, b in pauses
            if start_s <= a and b <= start_s + length_s
        ]
    return detect


# ---------------------------------------------------------------------------
# parse_silencedetect
# ---------------------------------------------------------------------------


def test_parse_silencedetect_pairs_starts_with_ends():
    stderr = (
        "[silencedetect @ 0x1] silence_start: -0.0123\n"
        "[silencedetect @ 0x1] silence_end: 0.61 | silence_duration: 0.62\n"
        "size=N/A time=00:00:10.00 bitrate=N/A\n"
        "[silencedetect @ 0x1] silence_start: 2.3205\n"
        "[silencedetect @ 0x1] silence_end: 2.8712 | silence_duration: 0.55\n"
        "[silencedetect @ 0x1] silence_start: 9.5\n"  # runs past the window: no end
    )
    assert tt.parse_silencedetect(stderr) == [(-0.0123, 0.61), (2.3205, 2.8712)]


def test_parse_silencedetect_of_nothing_is_empty():
    assert tt.parse_silencedetect("") == []


# ---------------------------------------------------------------------------
# estimate_offset
# ---------------------------------------------------------------------------


def test_estimate_offset_finds_a_transcript_running_early():
    starts, pauses = _speech(1, 150)
    boundaries = [s - 4.8 for s in starts]
    est = tt.estimate_offset(boundaries, pauses)
    assert est.shift_s == pytest.approx(4.8, abs=0.2)
    assert est.hits_at_best > est.hits_at_zero


def test_estimate_offset_finds_a_transcript_running_late():
    starts, pauses = _speech(2, 150)
    boundaries = [s + 3.0 for s in starts]
    assert tt.estimate_offset(boundaries, pauses).shift_s == pytest.approx(-3.0, abs=0.2)


def test_estimate_offset_of_an_aligned_transcript_is_near_zero():
    starts, pauses = _speech(3, 150)
    assert abs(tt.estimate_offset(starts, pauses).shift_s) <= 0.2


def test_estimate_offset_prefers_the_smallest_shift_on_a_tie():
    """Perfectly periodic speech scores the same at every multiple of its
    period. A tie must resolve to the shift nearest zero, not to whichever
    end of the search range comes first, or a healthy book would be
    reported several seconds out."""
    pauses = [(3.0 * k + 2.5, 3.0 * k + 3.0) for k in range(50)]
    boundaries = [3.0 * k + 2.75 for k in range(50)]
    assert tt.estimate_offset(boundaries, pauses).shift_s == pytest.approx(0.0, abs=0.15)


# ---------------------------------------------------------------------------
# check_transcript_timing — the verdict
# ---------------------------------------------------------------------------

DURATION_S = 4000


def test_a_transcript_seconds_early_everywhere_is_flagged():
    starts, pauses = _speech(10, DURATION_S)
    verdict = tt.check_transcript_timing(
        "/fake.m4b", _sentences_json(starts, 4.9), DURATION_S, detect=_detector(pauses),
    )
    assert verdict.ok is False
    assert verdict.shift_s == pytest.approx(4.9, abs=0.3)
    assert "early" in verdict.detail
    assert "re-transcribe" in verdict.detail.lower()


def test_a_transcript_seconds_late_everywhere_is_flagged_as_late():
    starts, pauses = _speech(11, DURATION_S)
    verdict = tt.check_transcript_timing(
        "/fake.m4b", _sentences_json(starts, -3.0), DURATION_S, detect=_detector(pauses),
    )
    assert verdict.ok is False
    assert "late" in verdict.detail


def test_an_aligned_transcript_passes():
    starts, pauses = _speech(12, DURATION_S)
    verdict = tt.check_transcript_timing(
        "/fake.m4b", _sentences_json(starts, 0.0), DURATION_S, detect=_detector(pauses),
    )
    assert verdict.ok is True


def test_ordinary_whisper_slack_passes():
    """Whisper puts a boundary a few hundred ms either side of the pause. On
    real healthy books the best shift sits within about half a second of
    zero; that must never flag."""
    starts, pauses = _speech(13, DURATION_S)
    verdict = tt.check_transcript_timing(
        "/fake.m4b", _sentences_json(starts, -0.4), DURATION_S, detect=_detector(pauses),
    )
    assert verdict.ok is True


def test_one_odd_window_does_not_flag_a_book():
    """A single window can find a spurious best shift (music, a scene break,
    dense dialogue); seen on a real healthy book as +1.4 s in one window and
    -0.4 s in the other four. The verdict needs the windows to agree."""
    starts, pauses = _speech(14, DURATION_S)
    windows = tt.window_starts(DURATION_S)
    odd = windows[0]
    # Shift only the transcript sentences inside the first window.
    shifted = [s - 4.0 if odd <= s < odd + tt.WINDOW_SECONDS else s for s in starts]
    rows = json.loads(_sentences_json(starts, 0.0))
    for row, s in zip(rows, shifted):
        row["start_ms"] = int(round(s * 1000))
    verdict = tt.check_transcript_timing(
        "/fake.m4b", json.dumps(rows), DURATION_S, detect=_detector(pauses),
    )
    assert verdict.ok is True


def test_a_book_too_short_to_sample_cannot_be_judged():
    starts, pauses = _speech(15, 60)
    verdict = tt.check_transcript_timing(
        "/fake.m4b", _sentences_json(starts, 4.9), 60, detect=_detector(pauses),
    )
    assert verdict.ok is None


def test_windows_with_no_pauses_cannot_be_judged():
    """Music, or a file the noise floor doesn't suit: no evidence either way,
    which is not the same as a pass or a fail."""
    starts, _ = _speech(16, DURATION_S)
    verdict = tt.check_transcript_timing(
        "/fake.m4b", _sentences_json(starts, 4.9), DURATION_S, detect=lambda *a: [],
    )
    assert verdict.ok is None


def test_an_unreadable_transcript_cannot_be_judged():
    verdict = tt.check_transcript_timing(
        "/fake.m4b", "not json", DURATION_S, detect=lambda *a: [],
    )
    assert verdict.ok is None


def test_window_starts_spread_across_the_book():
    starts = tt.window_starts(10_000)
    assert len(starts) == len(tt.WINDOW_FRACTIONS)
    assert starts == sorted(starts)
    assert starts[0] > 0 and starts[-1] + tt.WINDOW_SECONDS <= 10_000
