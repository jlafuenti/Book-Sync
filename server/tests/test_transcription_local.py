"""
Local transcription on faster-whisper, with word timing (issue #843).

The local provider used to run openai-whisper and group its dict segments by
character count. It now runs faster-whisper, whose segments are objects with a
`words` list, and groups them the way the Jetson worker does
(`jetson/server.py`): sentences come from the word timestamps, and each keeps
the words it was built from. The two trees cannot share a module, so these
tests mirror `jetson/test_server.py`'s word-grouping cases.

faster-whisper is not installed in the test image, so the module is stubbed in
`sys.modules` wherever a test reaches the model.
"""

import json
import re
import sys
import types

import pytest

from services import transcription as tx
from services import transcript_words


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWord:
    """Stands in for faster-whisper's Word (word_timestamps=True)."""

    def __init__(self, word, start, end, probability=0.9):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _FakeSegment:
    def __init__(self, text, start, end, words=None):
        self.text = text
        self.start = start
        self.end = end
        # A segment from a transcribe() call made without word_timestamps has
        # no `words` attribute at all; leave it off unless the test gives some.
        if words is not None:
            self.words = words


def _words(*triples):
    """_words((" the", 0.0, 0.2, 0.9), ...) -> list of _FakeWord."""
    return [_FakeWord(*t) for t in triples]


def _assert_word_invariant(sentences):
    for s in sentences:
        assert s.words, f"sentence without words: {s.text!r}"
        assert s.text.split() == [w["text"] for w in s.words]
        assert s.start_ms == s.words[0]["start_ms"]
        assert s.end_ms == s.words[-1]["end_ms"]


@pytest.fixture(autouse=True)
def real_sentence_split(monkeypatch):
    """Swap in a splitter that, like punkt, preserves the text and cuts after
    sentence-ending punctuation, and keep the corpus download out of the test."""
    monkeypatch.setattr(tx, "ensure_punkt", lambda: None)
    monkeypatch.setattr(
        tx.nltk, "sent_tokenize",
        lambda text: [p for p in re.split(r"(?<=[.!?])\s+", text) if p],
    )


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_each_segment_with_words_becomes_one_sentence():
    segments = [
        _FakeSegment(" The lamp was lit.", 0.0, 2.0, _words(
            (" The", 0.0, 0.3, 0.9), (" lamp", 0.4, 0.8, 0.8),
            (" was", 0.9, 1.2, 0.7), (" lit.", 1.3, 2.0, 0.6),
        )),
        _FakeSegment(" Nobody came.", 3.0, 4.0, _words(
            (" Nobody", 3.0, 3.5, 0.9), (" came.", 3.6, 4.0, 0.9),
        )),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["The lamp was lit.", "Nobody came."]
    assert [(s.start_ms, s.end_ms) for s in out] == [(0, 2000), (3000, 4000)]
    assert out[0].words[1] == {
        "text": "lamp", "start_ms": 400, "end_ms": 800, "probability": 0.8,
    }
    _assert_word_invariant(out)


def test_a_sentence_spanning_two_segments_is_merged():
    segments = [
        _FakeSegment(" Since I knew you, I", 0.0, 2.0, _words(
            (" Since", 0.0, 0.3, 0.9), (" I", 0.4, 0.5, 0.9), (" knew", 0.6, 0.9, 0.9),
            (" you,", 1.0, 1.3, 0.9), (" I", 1.4, 1.5, 0.9),
        )),
        _FakeSegment(" have been troubled.", 2.0, 3.0, _words(
            (" have", 2.0, 2.3, 0.9), (" been", 2.4, 2.6, 0.9), (" troubled.", 2.7, 3.0, 0.9),
        )),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["Since I knew you, I have been troubled."]
    assert len(out[0].words) == 8
    assert (out[0].start_ms, out[0].end_ms) == (0, 3000)
    _assert_word_invariant(out)


def test_a_segment_with_two_sentences_splits_on_the_word_boundary():
    # Character proration would put the boundary near 2.4s, nowhere near the
    # real 1.5s: the second sentence is one short word after a long pause.
    segments = [
        _FakeSegment(" The door was open. No.", 0.0, 3.0, _words(
            (" The", 0.0, 0.2, 0.9), (" door", 0.3, 0.6, 0.9),
            (" was", 0.7, 0.9, 0.9), (" open.", 1.0, 1.5, 0.9),
            (" No.", 2.5, 3.0, 0.9),
        )),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["The door was open.", "No."]
    assert [(s.start_ms, s.end_ms) for s in out] == [(0, 1500), (2500, 3000)]
    _assert_word_invariant(out)


def test_hyphenated_pieces_are_glued_into_one_word():
    segments = [
        _FakeSegment(" parchment-pale skin.", 0.0, 2.0, _words(
            (" parchment", 0.0, 0.5, 0.9), ("-pale", 0.5, 0.9, 0.4), (" skin.", 1.0, 1.5, 0.9),
        )),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["parchment-pale", "skin."]
    glued = out[0].words[0]
    assert (glued["start_ms"], glued["end_ms"], glued["probability"]) == (0, 900, 0.4)
    assert out[0].text == "parchment-pale skin."
    _assert_word_invariant(out)


def test_a_hyphenated_word_split_across_a_segment_boundary_is_glued():
    segments = [
        _FakeSegment(" sallow", 0.0, 1.0, _words((" sallow", 0.0, 0.6, 0.9))),
        _FakeSegment("-faced men.", 1.0, 2.0, _words(
            ("-faced", 0.6, 1.0, 0.9), (" men.", 1.1, 1.6, 0.9),
        )),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["sallow-faced", "men."]
    assert out[0].words[0]["end_ms"] == 1000


def test_a_leading_unspaced_piece_with_no_predecessor_starts_a_word():
    segments = [_FakeSegment("-ish.", 0.0, 1.0, _words(("-ish.", 0.0, 1.0, 0.9)))]

    out = tx._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["-ish."]


def test_blank_words_are_dropped():
    segments = [
        _FakeSegment(" Hi.", 0.0, 1.0, _words(
            (" ", 0.0, 0.1, 0.9), (" Hi.", 0.2, 1.0, 0.9),
        )),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [w["text"] for w in out[0].words] == ["Hi."]
    assert out[0].start_ms == 200


def _run_of_words(n, start=0.0, step=0.5, gap_after=None, gap=2.0):
    """n words w0..w{n-1}, `step` apart; an extra `gap` after index gap_after."""
    out, t = [], start
    for i in range(n):
        out.append(_FakeWord(f" w{i}", t, t + 0.25, 0.9))
        t += step + (gap if i == gap_after else 0.0)
    return out


def test_a_sentence_over_the_word_cap_splits_at_its_largest_gap():
    # 60 words, no punctuation until the end, biggest pause after the 20th.
    segments = [_FakeSegment(" long.", 0.0, 40.0, _run_of_words(60, gap_after=19))]

    out = tx._group_words_into_sentences(segments)

    assert [len(s.words) for s in out] == [20, 40]
    assert all(len(s.words) <= tx.MAX_SENTENCE_WORDS for s in out)
    _assert_word_invariant(out)


def test_the_word_cap_recurses_until_every_piece_fits():
    segments = [_FakeSegment(" long.", 0.0, 80.0, _run_of_words(130, gap_after=100))]

    out = tx._group_words_into_sentences(segments)

    assert sum(len(s.words) for s in out) == 130
    assert all(len(s.words) <= tx.MAX_SENTENCE_WORDS for s in out)
    # the pause after word 100 is the first cut: no sentence straddles it
    assert any(s.words[-1]["text"] == "w100" for s in out)
    _assert_word_invariant(out)


def test_word_cap_ties_go_to_the_earliest_gap():
    segments = [_FakeSegment(" long.", 0.0, 40.0, _run_of_words(60))]  # identical gaps

    out = tx._group_words_into_sentences(segments)

    assert len(out[0].words) == 1  # earliest gap: right after the first word
    assert all(len(s.words) <= tx.MAX_SENTENCE_WORDS for s in out)


def test_exactly_the_cap_is_not_split():
    words = _run_of_words(tx.MAX_SENTENCE_WORDS, gap_after=10)

    out = tx._group_words_into_sentences([_FakeSegment(" long.", 0.0, 40.0, words)])

    assert len(out) == 1


def test_a_segment_without_words_falls_back_to_character_proration():
    segments = [
        _FakeSegment(" One here. Two here.", 0.0, 2.0),          # no attribute
        _FakeSegment(" Another one.", 3.0, 4.0, words=[]),        # empty list
    ]

    out = tx._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["One here.", "Two here.", "Another one."]
    assert all(s.words == [] for s in out)
    assert (out[-1].start_ms, out[-1].end_ms) == (3000, 4000)


def test_wordless_and_worded_segments_keep_their_order():
    segments = [
        _FakeSegment(" Before.", 0.0, 1.0),
        _FakeSegment(" Middle part.", 2.0, 3.0, _words(
            (" Middle", 2.0, 2.4, 0.9), (" part.", 2.5, 3.0, 0.9),
        )),
        _FakeSegment(" After.", 4.0, 5.0, words=[]),
    ]

    out = tx._group_words_into_sentences(segments)

    assert [s.text for s in out] == ["Before.", "Middle part.", "After."]
    assert [bool(s.words) for s in out] == [False, True, False]


def test_a_tokenizer_that_rewrites_the_text_falls_back_to_counting_words(monkeypatch):
    monkeypatch.setattr(tx.nltk, "sent_tokenize", lambda text: ["REWRITTEN ONE", "TWO"])
    segments = [_FakeSegment(" a b c.", 0.0, 3.0, _words(
        (" a", 0.0, 1.0), (" b", 1.0, 2.0), (" c.", 2.0, 3.0),
    ))]

    out = tx._group_words_into_sentences(segments)

    assert [len(s.words) for s in out] == [2, 1]
    assert sum(len(s.words) for s in out) == 3


def test_a_sentence_defaults_to_no_words():
    assert tx.TranscribedSentence("x", 0, 1).words == []


def test_the_server_can_store_the_words_it_produces():
    """The round trip through services.transcript_words (#835) accepts the
    local provider's output, probability included."""
    segments = [
        _FakeSegment(" parchment-pale skin.", 0.0, 2.0, _words(
            (" parchment", 0.0, 0.5, 0.9), ("-pale", 0.5, 0.9, 0.4), (" skin.", 1.0, 1.5, 0.9),
        )),
        _FakeSegment(" Before.", 3.0, 4.0),  # no words: stored as an empty entry
    ]
    sentences = tx._group_words_into_sentences(segments)

    encoded = transcript_words.encode_words(sentences)

    assert json.loads(encoded) == [
        [["parchment-pale", 0, 900], ["skin.", 1000, 1500]],
        [],
    ]
    decoded = [tx.TranscribedSentence(s.text, s.start_ms, s.end_ms) for s in sentences]
    transcript_words.attach_words(decoded, encoded)
    assert decoded[0].words[0] == {"text": "parchment-pale", "start_ms": 0, "end_ms": 900}


# ---------------------------------------------------------------------------
# Device and compute type
# ---------------------------------------------------------------------------


def _stub_ctranslate2(monkeypatch, cuda_devices):
    module = types.ModuleType("ctranslate2")
    module.get_cuda_device_count = lambda: cuda_devices
    monkeypatch.setitem(sys.modules, "ctranslate2", module)


def _hide_ctranslate2(monkeypatch):
    # A None entry makes `import ctranslate2` raise ImportError.
    monkeypatch.setitem(sys.modules, "ctranslate2", None)


def test_auto_picks_cuda_when_a_device_is_visible(monkeypatch):
    monkeypatch.setattr(tx.settings, "whisper_device", "auto")
    _stub_ctranslate2(monkeypatch, 1)
    assert tx._get_whisper_device() == "cuda"


def test_auto_picks_cpu_when_no_device_is_visible(monkeypatch):
    monkeypatch.setattr(tx.settings, "whisper_device", "auto")
    _stub_ctranslate2(monkeypatch, 0)
    assert tx._get_whisper_device() == "cpu"


def test_auto_picks_cpu_when_ctranslate2_is_missing(monkeypatch):
    monkeypatch.setattr(tx.settings, "whisper_device", "auto")
    _hide_ctranslate2(monkeypatch)
    assert tx._get_whisper_device() == "cpu"


def test_cuda_without_a_device_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(tx.settings, "whisper_device", "cuda")
    _stub_ctranslate2(monkeypatch, 0)
    assert tx._get_whisper_device() == "cpu"


def test_cuda_with_a_device_is_cuda(monkeypatch):
    monkeypatch.setattr(tx.settings, "whisper_device", "cuda")
    _stub_ctranslate2(monkeypatch, 2)
    assert tx._get_whisper_device() == "cuda"


def test_cpu_never_probes_for_cuda(monkeypatch):
    monkeypatch.setattr(tx.settings, "whisper_device", "cpu")
    _hide_ctranslate2(monkeypatch)
    assert tx._get_whisper_device() == "cpu"


def test_compute_type_follows_the_device():
    assert tx._compute_type_for("cuda") == "float16"
    assert tx._compute_type_for("cpu") == "int8"


# ---------------------------------------------------------------------------
# transcribe_audiobook, end to end with faster-whisper stubbed
# ---------------------------------------------------------------------------


class _FakeAudioChunk:
    """Stands in for the numpy array: `transcribe_audiobook` only asks for its
    length, and a real hour of 16 kHz samples is 57M floats."""

    def __init__(self, samples):
        self._samples = samples

    def __len__(self):
        return self._samples


class _FakeInfo:
    def __init__(self, language):
        self.language = language


class _FakeModel:
    def __init__(self, init_kwargs, per_chunk_segments, detected="fr"):
        self.init_kwargs = init_kwargs
        self.calls = []
        self._per_chunk = list(per_chunk_segments)
        self._detected = detected

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        segments = self._per_chunk.pop(0) if self._per_chunk else []
        # faster-whisper hands back a lazy generator, not a list.
        return (s for s in segments), _FakeInfo(self._detected)


CHUNK_SAMPLES = 3600 * 16000


@pytest.fixture
def fake_faster_whisper(monkeypatch, tmp_path):
    """Install a fake `faster_whisper` and a stubbed ffmpeg pipeline."""
    def _install(chunks, per_chunk_segments, duration=3700.0, detected="fr"):
        created = []

        def _whisper_model(name, **kwargs):
            model = _FakeModel({"name": name, **kwargs}, per_chunk_segments, detected)
            created.append(model)
            return model

        module = types.ModuleType("faster_whisper")
        module.WhisperModel = _whisper_model
        monkeypatch.setitem(sys.modules, "faster_whisper", module)

        monkeypatch.setattr(tx.settings, "whisper_model", "tiny")
        monkeypatch.setattr(tx.settings, "app_data_dir", str(tmp_path))
        monkeypatch.setattr(tx, "_get_whisper_device", lambda: "cpu")
        monkeypatch.setattr(tx, "_get_audio_duration", lambda path: duration)

        remaining = list(chunks)
        monkeypatch.setattr(
            tx, "load_audio_chunk",
            lambda path, start, dur, sr=16000: remaining.pop(0),
        )
        return created

    return _install


def _hello_segment(start=0.0):
    return _FakeSegment(" Hello there.", start, start + 2.0, _words(
        (" Hello", start, start + 0.5, 0.9), (" there.", start + 1.0, start + 2.0, 0.8),
    ))


def test_the_model_is_loaded_with_the_configured_size_device_and_cache(
    fake_faster_whisper, tmp_path
):
    created = fake_faster_whisper([_FakeAudioChunk(16000)], [[_hello_segment()]])

    tx.transcribe_audiobook("book.m4b")

    assert len(created) == 1
    kwargs = created[0].init_kwargs
    assert kwargs["name"] == "tiny"
    assert kwargs["device"] == "cpu"
    assert kwargs["compute_type"] == "int8"
    assert kwargs["download_root"] == str(tmp_path / "whisper")
    assert (tmp_path / "whisper").is_dir()


def test_transcribe_asks_for_word_timestamps_and_vad(fake_faster_whisper):
    created = fake_faster_whisper([_FakeAudioChunk(16000)], [[_hello_segment()]])

    tx.transcribe_audiobook("book.m4b")

    call = created[0].calls[0]
    assert call["word_timestamps"] is True
    assert call["vad_filter"] is True
    assert call["condition_on_previous_text"] is False


def test_detected_language_is_pinned_for_later_chunks(fake_faster_whisper):
    created = fake_faster_whisper(
        [_FakeAudioChunk(CHUNK_SAMPLES), _FakeAudioChunk(16000)],
        [[_hello_segment()], [_hello_segment()]],
        detected="fr",
    )

    tx.transcribe_audiobook("book.m4b")

    assert [c["language"] for c in created[0].calls] == [None, "fr"]


def test_a_configured_language_is_passed_on_every_chunk(fake_faster_whisper):
    created = fake_faster_whisper(
        [_FakeAudioChunk(CHUNK_SAMPLES), _FakeAudioChunk(16000)],
        [[_hello_segment()], [_hello_segment()]],
        detected="de",
    )

    tx.transcribe_audiobook("book.m4b", language="en")

    assert [c["language"] for c in created[0].calls] == ["en", "en"]


def test_words_and_sentences_are_offset_by_the_chunk_start(fake_faster_whisper):
    fake_faster_whisper(
        [_FakeAudioChunk(CHUNK_SAMPLES), _FakeAudioChunk(16000)],
        [[_hello_segment()], [_hello_segment()]],
    )

    sentences = tx.transcribe_audiobook("book.m4b")

    assert [s.text for s in sentences] == ["Hello there.", "Hello there."]
    first, second = sentences
    assert (first.start_ms, first.end_ms) == (0, 2000)
    assert (second.start_ms, second.end_ms) == (3_600_000, 3_602_000)
    assert [w["start_ms"] for w in second.words] == [3_600_000, 3_601_000]
    assert [w["end_ms"] for w in second.words] == [3_600_500, 3_602_000]
    assert second.words[1]["probability"] == 0.8
    _assert_word_invariant(sentences)


def test_an_empty_chunk_ends_the_transcription(fake_faster_whisper):
    created = fake_faster_whisper(
        [_FakeAudioChunk(CHUNK_SAMPLES), _FakeAudioChunk(0)],
        [[_hello_segment()], [_hello_segment()]],
    )

    sentences = tx.transcribe_audiobook("book.m4b")

    assert len(created[0].calls) == 1
    assert len(sentences) == 1


def test_progress_is_reported_from_the_segments_as_they_arrive(fake_faster_whisper):
    fake_faster_whisper(
        [_FakeAudioChunk(CHUNK_SAMPLES), _FakeAudioChunk(16000)],
        [[_hello_segment(), _hello_segment(10.0)], [_hello_segment(0.0)]],
        duration=3700.0,
    )
    seen = []

    tx.transcribe_audiobook("book.m4b", progress_callback=lambda f, d: seen.append((f, d)))

    fractions = [f for f, _ in seen]
    assert all(0.0 <= f <= 1.0 for f in fractions)
    assert all(d == 3700.0 for _, d in seen)
    assert fractions[-1] == 1.0
    # a segment ending 12s into the first chunk, then 2s into the second
    assert any(abs(f - 12.0 / 3700.0) < 1e-9 for f in fractions)
    assert any(abs(f - (3600.0 + 2.0) / 3700.0) < 1e-9 for f in fractions)
    # chunk-relative end times must not run backwards across the chunk joint
    assert fractions == sorted(fractions)
