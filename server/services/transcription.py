"""
Whisper Transcription Service

Transcribes audiobook files using OpenAI Whisper and returns
timestamped sentences.
"""

import logging
from dataclasses import dataclass, field
from typing import List

import torch
import whisper
import nltk

from config import settings

logger = logging.getLogger(__name__)

# Ensure NLTK sentence tokenizer data is available
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)


@dataclass
class TranscribedSentence:
    """A sentence extracted from Whisper transcription with timing info."""
    text: str
    start_ms: int  # Start time in milliseconds
    end_ms: int    # End time in milliseconds
    words: List[dict] = field(default_factory=list)  # Raw word-level data


def _get_whisper_device() -> str:
    """Determine which device to use for Whisper inference."""
    device_setting = settings.whisper_device.lower()

    if device_setting == "auto":
        if torch.cuda.is_available():
            logger.info("GPU detected — using CUDA for Whisper")
            return "cuda"
        else:
            logger.info("No GPU detected — using CPU for Whisper")
            return "cpu"
    elif device_setting == "cuda":
        if not torch.cuda.is_available():
            logger.warning("CUDA requested but not available — falling back to CPU")
            return "cpu"
        return "cuda"
    else:
        return "cpu"


def _group_words_into_sentences(segments: list) -> List[TranscribedSentence]:
    """
    Group Whisper word-level output into sentences using punctuation
    and NLTK sentence boundary detection.

    Whisper segments already have some sentence-level grouping,
    but we refine it using NLTK for consistency.
    """
    sentences = []

    for segment in segments:
        # Each segment from Whisper is roughly a phrase/sentence
        text = segment.get("text", "").strip()
        if not text:
            continue

        start_ms = int(segment.get("start", 0) * 1000)
        end_ms = int(segment.get("end", 0) * 1000)
        words = segment.get("words", [])

        # Use NLTK to split if the segment contains multiple sentences
        nltk_sentences = nltk.sent_tokenize(text)

        if len(nltk_sentences) <= 1:
            # Single sentence — use segment timing directly
            sentences.append(TranscribedSentence(
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                words=words,
            ))
        else:
            # Multiple sentences in one segment — split timing proportionally
            total_chars = sum(len(s) for s in nltk_sentences)
            current_start = start_ms
            duration = end_ms - start_ms

            for sent_text in nltk_sentences:
                # Proportional duration based on character count
                sent_duration = int(duration * len(sent_text) / total_chars) if total_chars > 0 else 0
                sent_end = current_start + sent_duration

                sentences.append(TranscribedSentence(
                    text=sent_text.strip(),
                    start_ms=current_start,
                    end_ms=sent_end,
                ))
                current_start = sent_end

    return sentences


def transcribe_audiobook(audio_path: str) -> List[TranscribedSentence]:
    """
    Transcribe an audiobook file using Whisper.

    Args:
        audio_path: Path to the audio file (MP3, M4A, FLAC, WAV, OGG, etc.)

    Returns:
        List of TranscribedSentence objects with text and timing info.
    """
    device = _get_whisper_device()
    model_name = settings.whisper_model

    logger.info(f"Loading Whisper model '{model_name}' on {device}...")
    model = whisper.load_model(model_name, device=device)

    logger.info(f"Transcribing: {audio_path}")
    result = model.transcribe(
        audio_path,
        word_timestamps=True,
        verbose=False,
    )

    segments = result.get("segments", [])
    logger.info(f"Whisper produced {len(segments)} segments")

    # Group into sentences
    sentences = _group_words_into_sentences(segments)
    logger.info(f"Grouped into {len(sentences)} sentences")

    return sentences
