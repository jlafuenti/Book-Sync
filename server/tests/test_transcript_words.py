"""
`services.transcript_words`: the compact on-disk form of per-word timing
(issue #835) and its round trip back onto `TranscribedSentence.words`.
"""

import json
import logging

from services.transcript_words import attach_words, encode_words
from services.transcription import TranscribedSentence


def _s(text, start, end, words=None):
    return TranscribedSentence(text=text, start_ms=start, end_ms=end, words=words or [])


def _w(text, start, end, probability=0.9):
    return {"text": text, "start_ms": start, "end_ms": end, "probability": probability}


def test_encode_is_parallel_to_sentences_and_drops_probability():
    sentences = [
        _s("One two.", 0, 900, [_w("One", 0, 400), _w("two.", 450, 900)]),
        _s("Three.", 1000, 1500, [_w("Three.", 1000, 1500)]),
    ]
    assert json.loads(encode_words(sentences)) == [
        [["One", 0, 400], ["two.", 450, 900]],
        [["Three.", 1000, 1500]],
    ]


def test_encode_keeps_an_empty_slot_for_a_sentence_without_words():
    sentences = [
        _s("One.", 0, 400, [_w("One.", 0, 400)]),
        _s("Two.", 500, 900),
    ]
    assert json.loads(encode_words(sentences)) == [[["One.", 0, 400]], []]


def test_encode_returns_none_when_no_sentence_has_words():
    assert encode_words([_s("One.", 0, 400), _s("Two.", 500, 900)]) is None
    assert encode_words([]) is None


def test_attach_round_trips_without_probability():
    sentences = [
        _s("One two.", 0, 900, [_w("One", 0, 400), _w("two.", 450, 900)]),
        _s("Three.", 1000, 1500, [_w("Three.", 1000, 1500)]),
    ]
    encoded = encode_words(sentences)
    fresh = [_s("One two.", 0, 900), _s("Three.", 1000, 1500)]
    attach_words(fresh, encoded)
    assert fresh[0].words == [
        {"text": "One", "start_ms": 0, "end_ms": 400},
        {"text": "two.", "start_ms": 450, "end_ms": 900},
    ]
    assert fresh[1].words == [{"text": "Three.", "start_ms": 1000, "end_ms": 1500}]


def test_attach_tolerates_none_and_leaves_words_alone():
    sentences = [_s("One.", 0, 400)]
    attach_words(sentences, None)
    assert sentences[0].words == []


def test_attach_tolerates_invalid_json_and_warns_once(caplog):
    sentences = [_s("One.", 0, 400), _s("Two.", 500, 900)]
    with caplog.at_level(logging.WARNING, logger="services.transcript_words"):
        attach_words(sentences, "{not json")
    assert all(s.words == [] for s in sentences)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_attach_tolerates_a_length_mismatch_and_attaches_nothing(caplog):
    sentences = [_s("One.", 0, 400), _s("Two.", 500, 900)]
    with caplog.at_level(logging.WARNING, logger="services.transcript_words"):
        attach_words(sentences, json.dumps([[["One.", 0, 400]]]))
    assert all(s.words == [] for s in sentences)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_attach_tolerates_a_wrong_shape(caplog):
    sentences = [_s("One.", 0, 400)]
    with caplog.at_level(logging.WARNING, logger="services.transcript_words"):
        attach_words(sentences, json.dumps({"a": 1}))
        attach_words(sentences, json.dumps([[["only", "two"]]]))
    assert sentences[0].words == []
