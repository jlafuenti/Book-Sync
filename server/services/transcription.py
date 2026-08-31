"""
Whisper Transcription Service

Transcribes audiobook files using OpenAI Whisper and returns
timestamped sentences. Includes real-time progress reporting.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import List, Callable, Optional

# torch and whisper are imported lazily inside the functions that use them
# (_get_whisper_device / model loading). Keeping them out of module scope lets
# the transcription-provider package — and the queue manager that depends on it —
# be imported without the heavy ML stack (e.g. in tests / CI).
import mutagen
import nltk

from services.nltk_data import ensure_punkt
import tqdm as tqdm_module

from config import settings

logger = logging.getLogger(__name__)



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
    
    We override display() to suppress console output (we only want the callback),
    and override update() to forward progress.
    """
    
    def __init__(self, *args, **kwargs):
        # Disable console output by setting file to devnull
        kwargs['disable'] = False  # Ensure tqdm logic runs
        super().__init__(*args, **kwargs)
        self._callback = getattr(_progress_callback_local, 'callback', None)
        self._total_duration_sec = getattr(_progress_callback_local, 'total_duration', None)
    
    def update(self, n=1):
        super().update(n)
        if self._callback and self.total and self.total > 0:
            fraction = self.n / self.total
            self._callback(fraction, self._total_duration_sec)
    
    def display(self, msg=None, pos=None):
        # Suppress the console progress bar output — we report via callback only
        pass
    
    def close(self):
        # Suppress the final newline/cleanup output
        self.disable = True
        super().close()


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
    import torch

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

        # Use NLTK to split if the segment contains multiple sentences.
        # Ensured here rather than at import (issue #322).
        ensure_punkt()
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

def transcribe_audiobook(
    audio_path: str,
    progress_callback: Optional[Callable] = None,
) -> List[TranscribedSentence]:
    """
    Transcribe an audiobook file using Whisper, processing in chunks to avoid OOM.
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
    import whisper

    device = _get_whisper_device()
    model_name = settings.whisper_model

    logger.info(f"Loading Whisper model '{model_name}' on {device}...")
    if progress_callback:
        progress_callback(0.0, total_duration)  # Show "Downloading/loading model" phase
    
    model = whisper.load_model(model_name, device=device)
    logger.info(f"Whisper model loaded successfully")
    
    if progress_callback:
        progress_callback(0.0, total_duration)

    # Step 3: Transcribe in chunks
    logger.info(f"Transcribing: {audio_path}")

    CHUNK_SIZE_SEC = 3600  # 1 hour chunks
    all_sentences = []
    
    # We use a wrapper function for the chunk's progress callback to map it
    # accurately to the overall file's progress.
    for start_sec in range(0, int(total_duration) + 1 if total_duration else CHUNK_SIZE_SEC, CHUNK_SIZE_SEC):
        logger.info(f"Processing chunk {start_sec}s - {start_sec + CHUNK_SIZE_SEC}s")
        audio_array = load_audio_chunk(audio_path, start_sec, CHUNK_SIZE_SEC)
        
        if len(audio_array) == 0:
            logger.warning(f"Chunk at {start_sec}s returned no audio data. Ending transcription.")
            break

        # Define a chunk-specific progress callback
        # NOTE: _start=start_sec binds the current loop value by value (not reference)
        def _chunk_progress(fraction: float, _unused_duration: float, _start=start_sec):
            if not progress_callback:
                return
            if total_duration > 0:
                # fraction is 0 to 1 for this chunk
                chunk_elapsed = fraction * CHUNK_SIZE_SEC
                # cap it at total duration if this is the last chunk
                overall_elapsed = min(_start + chunk_elapsed, total_duration)
                overall_fraction = overall_elapsed / total_duration
                progress_callback(overall_fraction, total_duration)
        
        # Monkey-patch tqdm for this chunk
        _progress_callback_local.callback = _chunk_progress
        _progress_callback_local.total_duration = total_duration
        original_tqdm_class = tqdm_module.tqdm
        
        try:
            tqdm_module.tqdm = WhisperProgressBar
            result = model.transcribe(
                audio_array,
                word_timestamps=True,
                verbose=False,
            )
        finally:
            tqdm_module.tqdm = original_tqdm_class
            _progress_callback_local.callback = None
            _progress_callback_local.total_duration = None

        segments = result.get("segments", [])
        logger.info(f"Chunk produced {len(segments)} segments")

        # Offset the timestamps in the raw segments before grouping
        for seg in segments:
            seg["start"] += start_sec
            seg["end"] += start_sec
            if "words" in seg:
                for w in seg["words"]:
                    w["start"] += start_sec
                    w["end"] += start_sec

        # Group into sentences
        chunk_sentences = _group_words_into_sentences(segments)
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
