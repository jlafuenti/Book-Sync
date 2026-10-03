"""
Is a cached transcript in step with the audio file it is paired with?

The transcript cache (`services.queue_manager`) is keyed to the audiobook's
path, and a transcript written before issue #588 carries no fingerprint of the
audio it was made from. Replace or re-merge the file in place and every
timestamp can drift by a few seconds, stepping at the chapter joins of the new
encode, while the book's total length moves too little for the duration checks
to notice. A sync map aligned from that transcript is then a sentence or two
off everywhere; read-along highlights text the narrator has not reached.

This measures it against the audio directly. In a transcript that matches its
file, sentence boundaries fall in the pauses of the real recording. A few
windows spread across the book are sampled; ffmpeg's `silencedetect` finds
the pauses in each, and `estimate_offset` asks which time shift puts the
transcript's boundaries into them. A healthy book's best shift sits within
about half a second of zero in every window. A drifted one is seconds out, in
the same direction, in most of them.

`check_transcript_timing` is blocking (JSON parse plus one ffmpeg run per
window): call it through `asyncio.to_thread`.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

CHECK_TYPE = "transcript_timing"

#: Length of each sampled window, and where in the book they sit. Five
#: windows of 150 s each read about 12 minutes of audio per book, through an
#: input seek, so even a 30-hour book costs a few seconds.
WINDOW_SECONDS = 150
WINDOW_FRACTIONS = (0.1, 0.3, 0.5, 0.7, 0.9)

#: Search range and resolution for the shift. A re-encode that pads chapter
#: joins accumulates a few seconds; fifteen covers that with room, and is
#: still well short of the minutes-scale displacement the sync-map audit's own
#: timing check (issue #586) exists for.
MAX_SHIFT_SECONDS = 15.0
SHIFT_STEP_SECONDS = 0.1

#: How far outside a detected pause a boundary may sit and still count as in
#: it: Whisper's boundary and ffmpeg's pause edge rarely coincide exactly.
PAUSE_SLACK_SECONDS = 0.1

#: `silencedetect` settings: quieter than -35 dB for at least 0.2 s. Short
#: enough to catch the breath between sentences, long enough to skip the gaps
#: inside words.
SILENCE_NOISE_DB = -35
SILENCE_MIN_SECONDS = 0.2

#: A window needs at least this many transcript boundaries and pauses to say
#: anything; fewer (music, a long silence, the closing credits) is skipped.
MIN_BOUNDARIES_PER_WINDOW = 8
MIN_PAUSES_PER_WINDOW = 5

#: A window whose best shift is at least this far from zero is out of step.
OFFSET_THRESHOLD_SECONDS = 1.5

#: How many windows must be out of step, in the same direction, to flag the
#: book; and how many must be usable at all for a verdict. One odd window
#: (a scene break, dense dialogue) does not flag a book on its own.
MIN_DRIFTED_WINDOWS = 3
MIN_USABLE_WINDOWS = 3


@dataclass
class OffsetEstimate:
    """The best shift for one window. A positive `shift_s` means the real
    pauses sit later than the transcript says: the transcript runs early."""
    n: int
    hits_at_zero: int
    shift_s: float
    hits_at_best: int


@dataclass
class TimingVerdict:
    """`ok` is True (in step), False (out of step: re-transcribe) or None
    (cannot judge: too little evidence, which is not a pass)."""
    ok: Optional[bool]
    detail: str
    shift_s: Optional[float] = None
    windows: List[dict] = field(default_factory=list)


_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[0-9.]+)")


def parse_silencedetect(stderr: str) -> List[Tuple[float, float]]:
    """(start, end) pauses from ffmpeg's `silencedetect` log. A start with no
    end (silence running past the window) is dropped."""
    pauses: List[Tuple[float, float]] = []
    start: Optional[float] = None
    for kind, value in _SILENCE_RE.findall(stderr or ""):
        try:
            v = float(value)
        except ValueError:
            continue
        if kind == "start":
            start = v
        elif start is not None:
            pauses.append((start, v))
            start = None
    return pauses


def estimate_offset(
    boundaries: Sequence[float],
    pauses: Sequence[Tuple[float, float]],
    *,
    max_shift_s: float = MAX_SHIFT_SECONDS,
    step_s: float = SHIFT_STEP_SECONDS,
    slack_s: float = PAUSE_SLACK_SECONDS,
) -> OffsetEstimate:
    """The shift that puts the most `boundaries` inside a pause.

    Every shift across a pause's width scores the same, so the best score is
    a plateau, not a point; its middle is the estimate (the near edge would
    under-read every offset by up to a pause length). Evenly paced speech can
    reach the same best score on several plateaus; the one nearest zero wins,
    so such a book is not reported as out of step."""
    spans = sorted((a - slack_s, b + slack_s) for a, b in pauses)

    def hits(d: float) -> int:
        count = 0
        for x in boundaries:
            y = x + d
            for a, b in spans:
                if a > y:
                    break
                if y <= b:
                    count += 1
                    break
        return count

    steps = int(round(max_shift_s / step_s))
    shifts = [round(k * step_s, 3) for k in range(-steps, steps + 1)]
    scores = [hits(d) for d in shifts]
    best_hits = max(scores)

    # Contiguous runs of shifts at the best score; keep the run nearest zero.
    runs: List[Tuple[float, float]] = []
    run_start: Optional[float] = None
    for i, (d, h) in enumerate(zip(shifts, scores)):
        if h == best_hits and run_start is None:
            run_start = d
        if run_start is not None and (h != best_hits or i == len(shifts) - 1):
            run_end = d if h == best_hits else shifts[i - 1]
            runs.append((run_start, run_end))
            run_start = None

    def distance(run: Tuple[float, float]) -> float:
        lo, hi = run
        return 0.0 if lo <= 0.0 <= hi else min(abs(lo), abs(hi))

    lo, hi = min(runs, key=distance)
    return OffsetEstimate(
        n=len(boundaries), hits_at_zero=scores[steps],
        shift_s=round((lo + hi) / 2, 2), hits_at_best=best_hits,
    )


def window_starts(duration_s: float) -> List[int]:
    """Where each sampled window begins, in seconds."""
    return [int(duration_s * f) for f in WINDOW_FRACTIONS]


def detect_pauses(audio_path: str, start_s: float, length_s: float) -> List[Tuple[float, float]]:
    """Pauses in [start_s, start_s + length_s) of `audio_path`, relative to
    `start_s`. An input seek (`-ss` before `-i`): measured on real books it
    lands within 0.1 s of a full decode from the start, at a tiny fraction of
    the cost."""
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats",
        "-ss", f"{start_s:.3f}", "-t", f"{length_s:.3f}", "-i", audio_path,
        "-vn", "-af",
        f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                              errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"[transcript-timing] ffmpeg failed on {audio_path}: {e}")
        return []
    return parse_silencedetect(proc.stderr)


def _starts_s(sentences_json: str) -> Optional[List[float]]:
    try:
        rows = json.loads(sentences_json)
        return sorted(float(r["start_ms"]) / 1000.0 for r in rows if "start_ms" in r)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def check_transcript_timing(
    audio_path: str,
    sentences_json: str,
    duration_s: Optional[float],
    *,
    detect: Callable[[str, float, float], List[Tuple[float, float]]] = detect_pauses,
) -> TimingVerdict:
    """Sample the book and decide whether its transcript is in step with
    `audio_path`. Blocking; see the module docstring."""
    starts = _starts_s(sentences_json)
    if starts is None:
        return TimingVerdict(None, "Transcript could not be read")
    if not duration_s or duration_s < WINDOW_SECONDS * len(WINDOW_FRACTIONS):
        return TimingVerdict(None, "Too short to sample")

    windows: List[dict] = []
    usable: List[OffsetEstimate] = []
    for w in window_starts(duration_s):
        bounds = [s - w for s in starts if w <= s < w + WINDOW_SECONDS]
        pauses = detect(audio_path, w, WINDOW_SECONDS)
        if len(bounds) < MIN_BOUNDARIES_PER_WINDOW or len(pauses) < MIN_PAUSES_PER_WINDOW:
            windows.append({"at_s": w, "boundaries": len(bounds), "pauses": len(pauses)})
            continue
        est = estimate_offset(bounds, pauses)
        usable.append(est)
        windows.append({"at_s": w, "boundaries": est.n, "pauses": len(pauses),
                        "shift_s": est.shift_s, "hits_at_zero": est.hits_at_zero,
                        "hits_at_best": est.hits_at_best})

    if len(usable) < MIN_USABLE_WINDOWS:
        return TimingVerdict(
            None, f"Not enough speech to judge ({len(usable)} of {len(windows)} windows usable)",
            windows=windows)

    early = [e.shift_s for e in usable if e.shift_s >= OFFSET_THRESHOLD_SECONDS]
    late = [e.shift_s for e in usable if e.shift_s <= -OFFSET_THRESHOLD_SECONDS]
    drifted = early if len(early) >= len(late) else late
    shifts = sorted(e.shift_s for e in usable)
    median = shifts[len(shifts) // 2]

    if len(drifted) < MIN_DRIFTED_WINDOWS:
        return TimingVerdict(True, f"In step (median shift {median:+.1f} s)", median, windows)

    typical = sorted(drifted)[len(drifted) // 2]
    direction = "early" if typical > 0 else "late"
    detail = (
        f"Transcript runs about {abs(typical):.1f} s {direction} against this audio file "
        f"({len(drifted)} of {len(usable)} sampled windows), so its sync map is off by "
        f"about that much. Usually the file was replaced or re-encoded after it was "
        f"transcribed. Re-transcribe this pair."
    )
    return TimingVerdict(False, detail, typical, windows)
