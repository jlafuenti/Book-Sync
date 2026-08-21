"""
`probe_duration_seconds` — the ffprobe half of issue #127.

The first cut of #127 read the length from mutagen's `info.length` alone.
Verifying the deploy against the real library showed why that is not enough
(308 audiobooks, every file cross-checked against ffprobe):

  - 290 files: mutagen and ffprobe agreed exactly.
  - 17 files: `mutagen.File()` raised `MP4MetadataError` on a legacy Nero
    `chpl` atom, so mutagen produced nothing at all — while ffprobe read
    every one of them without complaint.
  - 1 file: an MP3 with a broken header where mutagen confidently returned
    **12.0 seconds** for a 9.9-hour book. That one is the dangerous case, not
    the blank ones: a stored duration of 11 makes `_in_audio_end_zone`
    (`duration*1000 - position <= 120_000`) true for *every* position, so the
    first audio write would silently mark the book finished.

So ffprobe is the authority and mutagen is the fallback for when ffprobe
isn't on PATH. ffmpeg is already a hard dependency of this server
(services.audio_integrity, services.chapter_repair).
"""

import subprocess

import pytest

from services.audio_duration import probe_duration_seconds


class _Completed:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture
def fake_ffprobe(monkeypatch):
    """Stand in for the ffprobe subprocess with a canned result or exception."""
    calls = []

    def _install(stdout="", returncode=0, raises=None):
        def _run(cmd, **kw):
            calls.append(cmd)
            if raises is not None:
                raise raises
            return _Completed(stdout=stdout, returncode=returncode)

        monkeypatch.setattr(subprocess, "run", _run)
        return calls

    return _install


def test_parses_the_duration_ffprobe_prints(fake_ffprobe):
    calls = fake_ffprobe(stdout="35648.208000\n")

    assert probe_duration_seconds("/x/book.mp3") == pytest.approx(35648.208)
    assert calls[0][0] == "ffprobe"
    assert "/x/book.mp3" in calls[0]


def test_ffprobe_missing_from_the_environment_returns_none(fake_ffprobe):
    """Not an error — the caller falls back to mutagen. Matches how
    services.audio_integrity treats a missing binary: a config problem, not a
    verdict about the file."""
    fake_ffprobe(raises=FileNotFoundError("ffprobe"))

    assert probe_duration_seconds("/x/book.m4b") is None


def test_a_timeout_returns_none(fake_ffprobe):
    fake_ffprobe(raises=subprocess.TimeoutExpired(cmd="ffprobe", timeout=120))

    assert probe_duration_seconds("/x/book.m4b") is None


def test_a_failing_probe_returns_none(fake_ffprobe):
    fake_ffprobe(stdout="", returncode=1)

    assert probe_duration_seconds("/x/broken.m4b") is None


@pytest.mark.parametrize("stdout", ["", "\n", "N/A\n", "not-a-number\n"])
def test_output_that_is_not_a_number_returns_none(fake_ffprobe, stdout):
    """ffprobe prints `N/A` for a container it opened but could not measure."""
    fake_ffprobe(stdout=stdout)

    assert probe_duration_seconds("/x/book.m4b") is None


@pytest.mark.parametrize("stdout", ["0.000000\n", "-1\n"])
def test_a_non_positive_duration_returns_none(fake_ffprobe, stdout):
    fake_ffprobe(stdout=stdout)

    assert probe_duration_seconds("/x/book.m4b") is None
