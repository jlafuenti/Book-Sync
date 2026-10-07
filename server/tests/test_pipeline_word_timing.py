"""
The transcription pipeline stores per-word timing next to the transcript and
hands it back on a cache hit (issue #835). `sentences_json` keeps exactly its
old shape; the words live in `words_json`.
"""

import json

from models.book import PairStatus
from services import queue_manager
from services.transcription import TranscribedSentence
from tests.factories import make_book_pair
from tests.test_queue_manager import (
    TRANSCRIPT,
    _PipelineProvider,
    _install_pipeline,
    _seed_item,
    _transcripts_for,
)


def _with_words(rows):
    """TRANSCRIPT as sentences whose words are its whitespace tokens, 100 ms each."""
    out = []
    for text, start, end in rows:
        words = [
            {"text": tok, "start_ms": start + i * 100, "end_ms": start + i * 100 + 90,
             "probability": 0.9}
            for i, tok in enumerate(text.split())
        ]
        out.append(TranscribedSentence(text=text, start_ms=start, end_ms=end, words=words))
    return out


class _WordProvider(_PipelineProvider):
    async def transcribe(self, audio_path, progress_callback=None):
        await super().transcribe(audio_path, progress_callback)
        return _with_words(self.rows)


async def test_a_fresh_transcript_persists_words_json(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _WordProvider())

    await queue_manager._run_transcription_pipeline(item.id, pair.id)

    (row,) = await _transcripts_for(pair.id)
    assert row.words_json is not None
    words = json.loads(row.words_json)
    assert len(words) == len(TRANSCRIPT)
    first_text = TRANSCRIPT[0][0].split()
    assert words[0] == [
        [tok, i * 100, i * 100 + 90] for i, tok in enumerate(first_text)
    ]
    # sentences_json is exactly what it was before word timing.
    assert json.loads(row.sentences_json) == [
        {"text": t, "start_ms": s, "end_ms": e} for t, s, e in TRANSCRIPT
    ]


async def test_a_provider_without_words_leaves_words_json_null(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    item = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider())

    await queue_manager._run_transcription_pipeline(item.id, pair.id)

    (row,) = await _transcripts_for(pair.id)
    assert row.words_json is None


async def test_a_retranscription_replaces_words_json(db, monkeypatch):
    """The update branch: the stale row is rewritten in place, words included."""
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    first = await _seed_item(db, pair.id, status="pending")
    _install_pipeline(monkeypatch, _PipelineProvider())
    await queue_manager._run_transcription_pipeline(first.id, pair.id)
    (row,) = await _transcripts_for(pair.id)
    assert row.words_json is None

    # A rejection forces a fresh transcription over the existing row.
    from services import transcript_timing

    async def _reject(db_, pair_id, transcript):
        return "forced for the test"

    monkeypatch.setattr(transcript_timing, "current_rejection", _reject)
    second = await _seed_item(db, pair.id, status="pending")
    provider = _WordProvider()
    _install_pipeline(monkeypatch, provider)
    await queue_manager._run_transcription_pipeline(second.id, pair.id)

    assert provider.transcribe_calls == 1
    (row,) = await _transcripts_for(pair.id)
    assert row.words_json is not None


async def test_realign_attaches_the_stored_words(db, monkeypatch):
    from models.transcript import AudioTranscript
    from services import realign
    from services.epub_parser import EpubSentence
    from services.transcript_words import encode_words

    sentences = _with_words(TRANSCRIPT)
    pair = await make_book_pair(db, duration_seconds=14)
    db.add(AudioTranscript(
        pair_id=pair.id, audiobook_path="/x/a.m4b", sentence_count=len(sentences),
        sentences_json=json.dumps(
            [{"text": s.text, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in sentences]),
        words_json=encode_words(sentences),
    ))
    await db.commit()
    monkeypatch.setattr(
        "services.realign.extract_book_sentences",
        lambda _p: [EpubSentence(chapter=0, sentence_index=i, text=s.text)
                    for i, s in enumerate(sentences)],
    )
    seen = []
    real_align = realign.align_texts_with_diagnostics

    def _spy(epub_sentences, whisper_sentences):
        seen.append(whisper_sentences)
        return real_align(epub_sentences, whisper_sentences)

    monkeypatch.setattr("services.realign.align_texts_with_diagnostics", _spy)

    await realign.realign_pair_from_cached_transcript(db, pair.id)

    assert seen[0][1].words[0] == {"text": "pack", "start_ms": 4_000, "end_ms": 4_090}


async def test_a_cache_hit_attaches_the_stored_words(db, monkeypatch):
    pair = await make_book_pair(db, status=PairStatus.AUTO_MATCHED)
    first = await _seed_item(db, pair.id, status="pending")
    provider = _WordProvider()
    _install_pipeline(monkeypatch, provider)
    await queue_manager._run_transcription_pipeline(first.id, pair.id)

    seen = []

    def _align(epub_sentences, whisper_sentences):
        seen.append(whisper_sentences)
        from tests.test_queue_manager import FIRST_MAP, _aligned
        return _aligned(FIRST_MAP), None

    monkeypatch.setattr("services.alignment.align_texts_with_diagnostics", _align)
    second = await _seed_item(db, pair.id, status="pending")
    await queue_manager._run_transcription_pipeline(second.id, pair.id)

    assert provider.transcribe_calls == 1, "the second run must use the cache"
    sentences = seen[0]
    assert sentences[0].words[0] == {"text": "the", "start_ms": 0, "end_ms": 90}
    assert len(sentences[0].words) == len(TRANSCRIPT[0][0].split())
