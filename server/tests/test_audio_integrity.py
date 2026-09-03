"""
The pre-flight audio gate (issue #245).

`check_audio_integrity` is a gate, not an oracle: it must only report
corruption when it actually saw corruption. Two conditions used to be
misreported as a corrupt file, and because `_run_integrity_gates` turns a
False verdict into a permanent, non-retriable "re-import required" failure,
the operator was told to re-import a file that was fine:

  * ffmpeg missing from the environment (a dev/bare-metal install; the server
    image ships it) — stage 2's `FileNotFoundError` escaped the function
    entirely, even though stage 1 deliberately passes when ffprobe is missing.
  * a full decode that outran the timeout — reported "(likely corrupt)" when
    all it knows is that a slow or contended host did not finish in time.

Both now pass with an unverified detail. A genuinely corrupt file is still
caught here when the decode completes, and by the worker's own decode
(`transcription_providers/remote.py`) when it does not.
"""

import subprocess

from services import audio_integrity
from services.audio_integrity import check_audio_integrity, is_unverified

_HEALTHY_PROBE = subprocess.CompletedProcess(
    args=["ffprobe"], returncode=0, stdout="3600.0\n", stderr=""
)


def _stub_stages(monkeypatch, decode):
    """ffprobe (stage 1) always succeeds; `decode` drives stage 2.

    `decode` is either a zero-arg callable that raises, or the
    CompletedProcess the ffmpeg call should return.
    """
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd[0])
        if cmd[0] == "ffprobe":
            return _HEALTHY_PROBE
        if callable(decode):
            return decode()
        return decode

    monkeypatch.setattr(audio_integrity.subprocess, "run", fake_run)
    return calls


def test_missing_ffmpeg_skips_the_decode_stage_instead_of_raising(monkeypatch):
    def _boom():
        raise FileNotFoundError(2, "No such file or directory: 'ffmpeg'")

    calls = _stub_stages(monkeypatch, _boom)

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert calls == ["ffprobe", "ffmpeg"]
    assert ok is True
    assert "ffmpeg unavailable" in detail
    assert is_unverified(detail)


def test_decode_timeout_is_not_reported_as_corruption(monkeypatch):
    def _slow():
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800)

    _stub_stages(monkeypatch, _slow)

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert ok is True
    assert "corrupt" not in detail.lower()
    assert "could not verify" in detail
    assert is_unverified(detail)


def test_genuine_decode_errors_still_fail(monkeypatch):
    stderr = "\n".join(
        f"[aac @ 0x1] Invalid data found when processing input (frame {i})"
        for i in range(12)
    )
    _stub_stages(
        monkeypatch,
        subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr=stderr),
    )

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert ok is False
    assert "decode errors" in detail


def test_a_clean_decode_is_verified(monkeypatch):
    """The other side of `is_unverified`: a real decode is not a warning."""
    _stub_stages(
        monkeypatch,
        subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr=""),
    )

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert ok is True
    assert is_unverified(detail) is False
