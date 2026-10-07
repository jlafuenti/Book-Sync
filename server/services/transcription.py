"""
Local Whisper transcription service

Transcribes audiobook files with faster-whisper (CTranslate2) and returns
timestamped sentences that carry the words they were built from. Includes
progress reporting.

This is the local provider's engine; the Jetson worker (`jetson/server.py`)
does the same job on its own host, and the word-grouping below is a port of
the worker's. The two trees cannot share a module, so a change to one belongs
in the other (issue #843).
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Callable, Optional

# faster_whisper and ctranslate2 are imported lazily inside the functions that
# use them (_get_whisper_device / model loading). Keeping them out of module
# scope lets the transcription-provider package — and the queue manager that
# depends on it — be imported without the ML stack (e.g. in tests / CI, or in
# the default remote-only image).
import mutagen
import nltk

from services.nltk_data import ensure_punkt

from config import settings

logger = logging.getLogger(__name__)

# A "sentence" longer than this (Whisper went a long way without punctuation)
# is split at its biggest pauses; one of a hundred words is useless as a sync
# point. Same value as the worker's.
MAX_SENTENCE_WORDS = 50


@dataclass
class TranscribedSentence:
    """A sentence extracted from Whisper transcription with timing info."""
    text: str
    start_ms: int  # Start time in milliseconds
    end_ms: int    # End time in milliseconds
    words: List[dict] = field(default_factory=list)  # Raw word-level data


def _get_audio_duration(audio_path: str) -> Optional[float]:
    """Get the duration of an audio file in seconds using mutagen."""
    try:
        audio = mutagen.File(audio_path)
        if audio and audio.info:
            return audio.info.length
    except Exception as e:
        logger.warning(f"Could not get audio duration: {e}")
    return None


def _format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None:
        return "??:??"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _get_whisper_device() -> str:
    """Determine which device to use for Whisper inference.

    `auto` means CUDA only when CTranslate2 can see a GPU; anything that stops
    it from importing or probing (no GPU libraries, a CPU-only build) is CPU.
    """
    device_setting = settings.whisper_device.lower()

    if device_setting == "cpu":
        return "cpu"

    try:
        import ctranslate2
        cuda_available = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda_available = False

    if device_setting == "cuda":
        if not cuda_available:
            logger.warning("CUDA requested but not available — falling back to CPU")
            return "cpu"
        return "cuda"

    if device_setting == "auto":
        if cuda_available:
            logger.info("GPU detected — using CUDA for Whisper")
            return "cuda"
        logger.info("No GPU detected — using CPU for Whisper")
    return "cpu"


def _compute_type_for(device: str) -> str:
    """float16 on a GPU; int8 on CPU, where it is several times faster than
    float32 for a small accuracy cost."""
    return "float16" if device == "cuda" else "int8"


def _group_segments_into_sentences(segments: list) -> List[TranscribedSentence]:
    """
    Group segments that carry no words into sentences by character count.

    The fallback for a segment the model returned without word timestamps:
    NLTK splits it, and the segment's time is shared between the sentences in
    proportion to their length. The sentences have `words: []`.
    """
    sentences: List[TranscribedSentence] = []

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue

        start_ms = int(segment.start * 1000)
        end_ms = int(segment.end * 1000)

        # Ensured here rather than at import (issue #322).
        ensure_punkt()
        nltk_sentences = nltk.sent_tokenize(text)

        if len(nltk_sentences) <= 1:
            sentences.append(TranscribedSentence(text=text, start_ms=start_ms, end_ms=end_ms))
            continue

        total_chars = sum(len(s) for s in nltk_sentences)
        current_start = start_ms
        duration = end_ms - start_ms
        for sent_text in nltk_sentences:
            sent_duration = int(duration * len(sent_text) / total_chars) if total_chars > 0 else 0
            sent_end = current_start + sent_duration
            sentences.append(TranscribedSentence(
                text=sent_text.strip(),
                start_ms=current_start,
                end_ms=sent_end,
            ))
            current_start = sent_end

    return sentences


def _glue_words(segments: list) -> List[dict]:
    """
    Flatten the segments' words into one ordered list of word dicts.

    faster-whisper emits hyphenated compounds, and some punctuation, as
    separate pieces whose text has no leading space (" parchment", "-pale").
    A piece without a leading space belongs to the word before it, even when
    that word is in the previous segment: a segment boundary can fall inside
    a hyphenated word. Only when there is no previous word at all does an
    unspaced piece start one. The glued word keeps the first piece's start and
    takes the last piece's end and probability.
    """
    pieces: List[list] = []  # [raw text, start_ms, end_ms, probability]
    for segment in segments:
        for w in segment.words:
            raw = w.word
            if not raw:
                continue
            start_ms, end_ms = int(w.start * 1000), int(w.end * 1000)
            probability = float(w.probability)
            if pieces and not raw[0].isspace():
                prev = pieces[-1]
                prev[0] += raw
                prev[2] = end_ms
                prev[3] = probability
            else:
                pieces.append([raw, start_ms, end_ms, probability])

    words = []
    for raw, start_ms, end_ms, probability in pieces:
        text = raw.strip()
        if text:
            words.append({
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "probability": probability,
            })
    return words


def _split_at_largest_gaps(words: List[dict]) -> List[List[dict]]:
    """
    Cut a run of words into pieces of at most MAX_SENTENCE_WORDS.

    Whisper sometimes goes a long way without punctuation, and one "sentence"
    of a hundred words is useless as a sync point. Cut where the speaker
    paused longest (`next.start - prev.end`), and recurse on each side until
    everything fits. `max` returns the first of equal gaps, so ties go to the
    earliest.
    """
    if len(words) <= MAX_SENTENCE_WORDS:
        return [words]
    cut = max(
        range(1, len(words)),
        key=lambda i: words[i]["start_ms"] - words[i - 1]["end_ms"],
    )
    return _split_at_largest_gaps(words[:cut]) + _split_at_largest_gaps(words[cut:])


def _sentences_from_words(words: List[dict]) -> List[TranscribedSentence]:
    """One TranscribedSentence per piece of `words` once the word cap is applied."""
    return [
        TranscribedSentence(
            text=" ".join(w["text"] for w in piece),
            start_ms=piece[0]["start_ms"],
            end_ms=piece[-1]["end_ms"],
            words=piece,
        )
        for piece in _split_at_largest_gaps(words)
    ]


def _sentences_from_word_segments(segments: list) -> List[TranscribedSentence]:
    """Sentences for a run of consecutive segments that all carry words."""
    words = _glue_words(segments)
    if not words:
        return []

    # Joined on single spaces, so every word boundary is a space and NLTK's
    # (text-preserving) sentences map back onto whole runs of words.
    # Ensured here rather than at import (issue #322).
    ensure_punkt()
    joined = " ".join(w["text"] for w in words)
    ends = []
    pos = 0
    for w in words:
        pos += len(w["text"])
        ends.append(pos)
        pos += 1

    sentences: List[TranscribedSentence] = []
    cursor = 0   # where to look for the next sentence in `joined`
    first = 0    # index of the first word not yet assigned to a sentence
    for sent_text in nltk.sent_tokenize(joined):
        sent_text = sent_text.strip()
        if not sent_text:
            continue
        if first >= len(words):
            break
        found = joined.find(sent_text, cursor)
        if found >= 0:
            cursor = found + len(sent_text)
            last = first
            while last < len(words) and ends[last] <= cursor:
                last += 1
            last = max(last, first + 1)
        else:
            # A tokenizer that rewrote the text. Fall back to counting words.
            last = min(first + max(1, len(sent_text.split())), len(words))
        sentences.extend(_sentences_from_words(words[first:last]))
        first = last

    if first < len(words):  # words the tokenizer's sentences did not account for
        sentences.extend(_sentences_from_words(words[first:]))
    return sentences


def _group_words_into_sentences(segments_list: list) -> List[TranscribedSentence]:
    """
    Group faster-whisper segments into sentences from their word timestamps.

    Sentences are found by running NLTK over the words of the whole chunk, not
    one segment at a time, so a sentence that straddles a segment boundary
    stays whole, and each sentence's timing is its first word's start and its
    last word's end rather than a character-count guess. Each sentence keeps
    the words it was built from.

    A segment with no words (the model was run without word timestamps, or
    returned none for it) goes through _group_segments_into_sentences instead,
    in order, with `words: []`.
    """
    sentences: List[TranscribedSentence] = []
    run: list = []  # consecutive segments that carry words

    for segment in segments_list:
        if getattr(segment, "words", None):
            run.append(segment)
            continue
        if run:
            sentences.extend(_sentences_from_word_segments(run))
            run = []
        sentences.extend(_group_segments_into_sentences([segment]))

    if run:
        sentences.extend(_sentences_from_word_segments(run))
    return sentences


def load_audio_chunk(file: str, start_sec: int, duration_sec: int, sr: int = 16000):
    """Load a specific time chunk of audio as a numpy array using ffmpeg."""
    import subprocess
    import numpy as np
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-threads", "0",
        "-ss", str(start_sec),
        "-i", file,
        "-t", str(duration_sec),
        # Follow the file's timestamps, not its sample count: overlapping
        # frame timestamps at a merged m4b's part joins otherwise add samples
        # and drift every timestamp in the chunk late (issue #795).
        "-af", "aresample=async=1",
        "-f", "s16le",
        "-ac", "1",
        "-acodec", "pcm_s16le",
        "-ar", str(sr),
        "-"
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed: {e.stderr.decode()}")
        raise RuntimeError(f"Failed to load audio chunk at {start_sec}s") from e

    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0

def _offset_sentences(sentences: List[TranscribedSentence], offset_ms: int) -> None:
    """Shift sentences, and the words inside them, from chunk time to file time."""
    if not offset_ms:
        return
    for sentence in sentences:
        sentence.start_ms += offset_ms
        sentence.end_ms += offset_ms
        for word in sentence.words:
            word["start_ms"] += offset_ms
            word["end_ms"] += offset_ms


def transcribe_audiobook(
    audio_path: str,
    progress_callback: Optional[Callable] = None,
    language: Optional[str] = None,
) -> List[TranscribedSentence]:
    """
    Transcribe an audiobook file with faster-whisper, in chunks to avoid OOM.

    `language` is an ISO 639-1 code forced on every chunk. Leaving it None
    detects the language on the first chunk and pins that answer for the rest
    of the file — never per-chunk re-detection (issue #246), which turns an
    hour that opens on music or a foreign epigraph into transliterated garbage
    that alignment then silently interpolates across.

    Each sentence carries the words it was built from (issue #843), the same
    shape the remote worker returns.
    """
    # Step 1: Get audio duration for progress reporting
    total_duration = _get_audio_duration(audio_path)
    if total_duration:
        logger.info(f"Audio duration: {_format_duration(total_duration)} ({total_duration:.1f}s)")
    else:
        logger.info("Could not determine audio duration — progress will be estimated")
        total_duration = 0

    # Notify callback of start
    if progress_callback:
        progress_callback(0.0, total_duration)

    # Step 2: Load model
    from faster_whisper import WhisperModel

    device = _get_whisper_device()
    compute_type = _compute_type_for(device)
    model_name = settings.whisper_model
    # Models are downloaded on first use; keep them under the app data volume
    # so a container rebuild does not fetch them again.
    model_dir = Path(settings.app_data_dir) / "whisper"
    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading Whisper model '{model_name}' on {device} ({compute_type})...")
    if progress_callback:
        progress_callback(0.0, total_duration)  # Show "Downloading/loading model" phase

    model = WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=str(model_dir),
    )
    logger.info("Whisper model loaded successfully")

    if progress_callback:
        progress_callback(0.0, total_duration)

    # Step 3: Transcribe in chunks
    logger.info(f"Transcribing: {audio_path}")

    CHUNK_SIZE_SEC = 3600  # 1 hour chunks
    all_sentences: List[TranscribedSentence] = []
    pinned_language = language or None
    logger.info(f"Language: {pinned_language or 'auto (detect once, then pin)'}")

    for start_sec in range(0, int(total_duration) + 1 if total_duration else CHUNK_SIZE_SEC, CHUNK_SIZE_SEC):
        logger.info(f"Processing chunk {start_sec}s - {start_sec + CHUNK_SIZE_SEC}s")
        audio_array = load_audio_chunk(audio_path, start_sec, CHUNK_SIZE_SEC)

        if len(audio_array) == 0:
            logger.warning(f"Chunk at {start_sec}s returned no audio data. Ending transcription.")
            break

        segments_iter, info = model.transcribe(
            audio_array,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
            language=pinned_language,
        )

        # Detect once, then pin: chunk 1's answer governs the rest of the file.
        if not pinned_language:
            detected = getattr(info, "language", None)
            if detected:
                pinned_language = detected
                logger.info(
                    f"Detected language '{detected}' — pinning it for the rest of this file"
                )

        # `segments_iter` is lazy: the audio is decoded as it is consumed, so
        # progress is reported from each segment's end as it arrives.
        segments = []
        for segment in segments_iter:
            segments.append(segment)
            if progress_callback and total_duration > 0:
                overall_elapsed = min(start_sec + segment.end, total_duration)
                progress_callback(overall_elapsed / total_duration, total_duration)
        logger.info(f"Chunk produced {len(segments)} segments")

        chunk_sentences = _group_words_into_sentences(segments)
        _offset_sentences(chunk_sentences, start_sec * 1000)
        logger.info(f"Chunk grouped into {len(chunk_sentences)} sentences")
        all_sentences.extend(chunk_sentences)

        # If audio array is smaller than chunk size, it was the last chunk
        expected_samples = CHUNK_SIZE_SEC * 16000
        if len(audio_array) < expected_samples * 0.99:
            break

        # Safety break if we couldn't properly read duration initially
        if total_duration == 0:
            break

    logger.info(f"Total transcription yielded {len(all_sentences)} sentences")

    # Final progress update
    if progress_callback:
        progress_callback(1.0, total_duration)

    return all_sentences
