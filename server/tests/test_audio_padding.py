"""
Padded audio: a container that states far more time than its stream holds
(issue #796).

An m4b stated ~10.3 h but held ~2.7 h of AAC: each chapter had 20-30 min of
audio, then nothing. ffmpeg's full decode follows the stream's timestamps
across the gaps, so it decoded "fully" to the stated length and the integrity
gate passed it. The sample count in the header is what gives it away:
`nb_frames x samples_per_frame / sample_rate` against `format=duration`.

ffprobe is stubbed throughout; the numbers are synthetic.
"""

import json
import subprocess

import pytest

from services import audio_integrity
from services.audio_integrity import (
    PADDED_AUDIO_MAX_RATIO,
    check_audio_integrity,
    probe_real_audio_seconds,
    sample_derived_seconds,
)

_CLEAN_DECODE = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")


def _probe_json(duration, codec="aac", profile="LC", sample_rate=22050, nb_frames=None):
    stream = {"codec_name": codec, "sample_rate": str(sample_rate)}
    if profile is not None:
        stream["profile"] = profile
    if nb_frames is not None:
        stream["nb_frames"] = str(nb_frames)
    return json.dumps({"programs": [], "streams": [stream],
                       "format": {"duration": f"{duration:.6f}"}})


def _frames_for(seconds, samples_per_frame=1024, sample_rate=22050):
    return int(round(seconds * sample_rate / samples_per_frame))


def _stub(monkeypatch, probe_stdout, decode=_CLEAN_DECODE):
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout=probe_stdout, stderr="")
        return decode

    monkeypatch.setattr(audio_integrity.subprocess, "run", fake_run)
    return calls


STATED = 37_000.0  # a ~10.3 h container


def test_the_threshold_is_the_one_the_measurement_supports():
    """Healthy files measured 0.929-1.050; the padded one 0.26. 0.8 sits well
    clear of both."""
    assert PADDED_AUDIO_MAX_RATIO == 0.80


def test_an_aac_file_holding_a_quarter_of_its_stated_length_fails(monkeypatch):
    real = STATED * 0.26
    calls = _stub(monkeypatch, _probe_json(STATED, nb_frames=_frames_for(real)))

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert ok is False
    assert detail.startswith("padded:")
    assert "2.7 h of audio" in detail
    assert "10.3 h" in detail
    # Header only: a padded file is caught without paying for the full decode.
    assert [c[0] for c in calls] == ["ffprobe"]


@pytest.mark.parametrize("ratio", [0.93, 1.0, 1.05])
def test_files_near_their_stated_length_pass(monkeypatch, ratio):
    _stub(monkeypatch, _probe_json(STATED, nb_frames=_frames_for(STATED * ratio)))

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert ok is True
    assert detail.startswith("ok")


def test_an_mp3_with_no_frame_count_is_not_judged(monkeypatch):
    _stub(monkeypatch, _probe_json(STATED, codec="mp3", profile=None,
                                   sample_rate=44100, nb_frames=None))

    ok, detail = check_audio_integrity("/audio/book.mp3")

    assert ok is True
    assert detail.startswith("ok")


def test_he_aac_counts_2048_samples_per_frame():
    """HE-AAC reports the doubled (SBR) sample rate, with 2048 samples per
    frame at that rate. Counting 1024 would halve the real length."""
    frames = _frames_for(STATED, samples_per_frame=2048, sample_rate=44100)
    stream = {"codec_name": "aac", "profile": "HE-AAC", "sample_rate": "44100",
              "nb_frames": str(frames)}

    assert sample_derived_seconds(stream) == pytest.approx(STATED, rel=1e-3)


def test_he_aac_v2_counts_2048_too():
    stream = {"codec_name": "aac", "profile": "HE-AACv2", "sample_rate": "44100",
              "nb_frames": "1000"}

    assert sample_derived_seconds(stream) == pytest.approx(1000 * 2048 / 44100)


def test_an_he_aac_file_at_its_full_length_passes(monkeypatch):
    frames = _frames_for(STATED, samples_per_frame=2048, sample_rate=44100)
    _stub(monkeypatch, _probe_json(STATED, profile="HE-AAC", sample_rate=44100,
                                   nb_frames=frames))

    ok, _detail = check_audio_integrity("/audio/book.m4b")

    assert ok is True


@pytest.mark.parametrize("stream", [
    {"codec_name": "aac", "profile": "LC", "sample_rate": "22050"},                  # no nb_frames
    {"codec_name": "aac", "profile": "LC", "sample_rate": "22050", "nb_frames": "N/A"},
    {"codec_name": "aac", "profile": "LC", "sample_rate": "0", "nb_frames": "100"},
    {"codec_name": "aac", "profile": "LC", "nb_frames": "100"},                      # no rate
    {"codec_name": "aac", "sample_rate": "22050", "nb_frames": "100"},               # no profile
    {"codec_name": "aac", "profile": "LD", "sample_rate": "22050", "nb_frames": "100"},
    {"codec_name": "opus", "sample_rate": "48000", "nb_frames": "100"},
    {"codec_name": "flac", "sample_rate": "44100", "nb_frames": "100"},
    {},
])
def test_unreadable_or_unknown_streams_are_not_judged(stream):
    assert sample_derived_seconds(stream) is None


def test_mp3_frame_sizes_follow_the_mpeg_version():
    """1152 samples per frame for MPEG-1 Layer III (32-48 kHz); 576 for the
    MPEG-2/2.5 rates below that."""
    assert sample_derived_seconds(
        {"codec_name": "mp3", "sample_rate": "44100", "nb_frames": "100"}
    ) == pytest.approx(100 * 1152 / 44100)
    assert sample_derived_seconds(
        {"codec_name": "mp3", "sample_rate": "22050", "nb_frames": "100"}
    ) == pytest.approx(100 * 576 / 22050)


@pytest.mark.parametrize("stdout", [
    json.dumps({"streams": [], "format": {}}),
    json.dumps({"streams": [], "format": {"duration": "N/A"}}),
    "not json",
])
def test_a_header_with_no_usable_duration_still_fails(monkeypatch, stdout):
    """Unchanged by the switch to JSON output: no duration, no pass."""
    _stub(monkeypatch, stdout)

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert ok is False
    assert "could not parse audio duration" in detail


def test_a_zero_duration_still_fails(monkeypatch):
    _stub(monkeypatch, json.dumps({"streams": [], "format": {"duration": "0.0"}}))

    ok, detail = check_audio_integrity("/audio/book.m4b")

    assert (ok, detail) == (False, "reported audio duration is zero")


def test_probe_real_audio_seconds_returns_the_sample_length(monkeypatch):
    _stub(monkeypatch, _probe_json(STATED, nb_frames=_frames_for(9_600)))

    assert probe_real_audio_seconds("/audio/book.m4b") == pytest.approx(9_600, abs=1)


def test_probe_real_audio_seconds_is_none_when_it_cannot_tell(monkeypatch):
    _stub(monkeypatch, _probe_json(STATED, codec="mp3", profile=None, sample_rate=44100))

    assert probe_real_audio_seconds("/audio/book.mp3") is None


def test_probe_real_audio_seconds_is_none_without_ffprobe(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(audio_integrity.subprocess, "run", fake_run)

    assert probe_real_audio_seconds("/audio/book.m4b") is None


def test_probe_real_audio_seconds_is_none_on_a_failed_probe(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="",
                                           stderr="No such file")

    monkeypatch.setattr(audio_integrity.subprocess, "run", fake_run)

    assert probe_real_audio_seconds("/audio/missing.m4b") is None
