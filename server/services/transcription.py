"""
Whisper Transcription Service

Transcribes audiobook files using OpenAI Whisper and returns
timestamped sentences. Includes real-time progress reporting.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import List, Callable, Optional

import torch
import whisper
import whisper.transcribe
import mutagen
import nltk
import tqdm as tqdm_module

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


# Thread-local storage for progress callbacks
_progress_callback_local = threading.local()


class WhisperProgressBar(tqdm_module.tqdm):
    """
    Custom tqdm replacement that intercepts Whisper's internal progress bar
    and forwards updates to our callback. Whisper processes audio in 30-second
    chunks, and its tqdm tracks frames processed vs total frames.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._callback = getattr(_progress_callback_local, 'callback', None)
        self._total_duration_sec = getattr(_progress_callback_local, 'total_duration', None)
    
    def update(self, n=1):
        super().update(n)
        if self._callback and self.total and self.total > 0:
            fraction = self.n / self.total
            self._callback(fraction, self._total_duration_sec)


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


def transcribe_audiobook(
    audio_path: str,
    progress_callback: Optional[Callable] = None,
) -> List[TranscribedSentence]:
    """
    Transcribe an audiobook file using Whisper.

    Args:
        audio_path: Path to the audio file (MP3, M4A, FLAC, WAV, OGG, etc.)
        progress_callback: Optional callback(fraction, total_duration_sec)
                          called periodically with transcription progress.
                          fraction is 0.0-1.0, total_duration_sec is the audio length.

    Returns:
        List of TranscribedSentence objects with text and timing info.
    """
    # Step 1: Get audio duration for progress reporting
    total_duration = _get_audio_duration(audio_path)
    if total_duration:
        logger.info(f"Audio duration: {_format_duration(total_duration)} ({total_duration:.1f}s)")
    else:
        logger.info("Could not determine audio duration — progress will be estimated")

    # Notify callback of start
    if progress_callback:
        progress_callback(0.0, total_duration)

    # Step 2: Load model
    device = _get_whisper_device()
    model_name = settings.whisper_model

    logger.info(f"Loading Whisper model '{model_name}' on {device}...")
    model = whisper.load_model(model_name, device=device)
    
    if progress_callback:
        progress_callback(0.0, total_duration)

    # Step 3: Transcribe with progress tracking
    # We monkey-patch tqdm in whisper.transcribe so Whisper's internal
    # progress bar reports to our callback instead of printing to console.
    logger.info(f"Transcribing: {audio_path}")

    # Set up thread-local progress callback
    _progress_callback_local.callback = progress_callback
    _progress_callback_local.total_duration = total_duration
    
    # Monkey-patch tqdm in whisper's transcribe module
    original_tqdm = getattr(whisper.transcribe, 'tqdm', tqdm_module)
    original_tqdm_class = tqdm_module.tqdm
    
    try:
        # Replace tqdm in whisper.transcribe module
        if hasattr(whisper.transcribe, 'tqdm'):
            # whisper imports tqdm as a module: from tqdm import tqdm
            # We need to replace the reference whisper uses
            whisper.transcribe.tqdm = WhisperProgressBar
        
        # Also patch the module-level tqdm.tqdm in case whisper uses it that way
        tqdm_module.tqdm = WhisperProgressBar
        
        result = model.transcribe(
            audio_path,
            word_timestamps=True,
            # verbose=None enables tqdm progress bar (so our hook works)
            # but does NOT print transcribed text to console.
            # verbose=False would disable tqdm entirely.
            verbose=None if progress_callback else False,
        )
    finally:
        # Restore original tqdm
        tqdm_module.tqdm = original_tqdm_class
        if hasattr(whisper.transcribe, 'tqdm'):
            whisper.transcribe.tqdm = original_tqdm
        _progress_callback_local.callback = None
        _progress_callback_local.total_duration = None

    segments = result.get("segments", [])
    logger.info(f"Whisper produced {len(segments)} segments")

    # Group into sentences
    sentences = _group_words_into_sentences(segments)
    logger.info(f"Grouped into {len(sentences)} sentences")

    # Final progress update
    if progress_callback:
        progress_callback(1.0, total_duration)

    return sentences
