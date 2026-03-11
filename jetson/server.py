"""
BookSync Transcription Server — faster-whisper on Jetson Orin Nano

A lightweight FastAPI server that accepts audio file uploads and returns
timestamped, sentence-segmented transcription results using faster-whisper.

Designed to run on a Jetson Orin Nano with GPU acceleration.
"""

import os
import time
import shutil
import logging
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

import nltk

# Ensure NLTK sentence tokenizer data is available
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)

# ---------------------------------------------------------------------------
# Configuration via environment variables
# ---------------------------------------------------------------------------
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
VAD_FILTER = os.environ.get("VAD_FILTER", "true").lower() in ("true", "1", "yes")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "9000"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("transcription-server")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TranscribedSentence:
    """A sentence with timing information."""
    text: str
    start_ms: int
    end_ms: int


@dataclass
class JobStatus:
    """Current status of the active transcription job."""
    active: bool = False
    progress: float = 0.0
    message: str = "Idle"
    started_at: Optional[float] = None  # time.time()


# Thread-safe global job status (only one job runs at a time)
_job_status = JobStatus()
_job_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Model loading (once at startup)
# ---------------------------------------------------------------------------
model = None


def load_model():
    """Load the faster-whisper model onto the GPU at startup."""
    global model
    from faster_whisper import WhisperModel

    logger.info(
        f"Loading faster-whisper model '{WHISPER_MODEL}' "
        f"(compute_type={WHISPER_COMPUTE_TYPE}, device={WHISPER_DEVICE})..."
    )
    start = time.time()
    model = WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )
    elapsed = time.time() - start
    logger.info(f"Model loaded in {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Transcription logic
# ---------------------------------------------------------------------------

def _group_segments_into_sentences(segments_list: list) -> List[TranscribedSentence]:
    """
    Group faster-whisper segments into sentences using NLTK.

    faster-whisper yields segments that are roughly phrase-level.
    We refine boundaries using NLTK sentence tokenization — identical
    logic to the Book Sync server's _group_words_into_sentences.
    """
    sentences: List[TranscribedSentence] = []

    for segment in segments_list:
        text = segment.text.strip()
        if not text:
            continue

        start_ms = int(segment.start * 1000)
        end_ms = int(segment.end * 1000)

        # Use NLTK to split if the segment contains multiple sentences
        nltk_sentences = nltk.sent_tokenize(text)

        if len(nltk_sentences) <= 1:
            sentences.append(TranscribedSentence(
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
            ))
        else:
            # Multiple sentences — split timing proportionally by char count
            total_chars = sum(len(s) for s in nltk_sentences)
            current_start = start_ms
            duration = end_ms - start_ms

            for sent_text in nltk_sentences:
                if total_chars > 0:
                    sent_duration = int(duration * len(sent_text) / total_chars)
                else:
                    sent_duration = 0
                sent_end = current_start + sent_duration

                sentences.append(TranscribedSentence(
                    text=sent_text.strip(),
                    start_ms=current_start,
                    end_ms=sent_end,
                ))
                current_start = sent_end

    return sentences


def _transcribe_file(audio_path: str) -> dict:
    """
    Run faster-whisper on the given audio file.
    Updates _job_status with progress as segments are yielded.
    Returns the response dict.
    """
    global _job_status

    start_time = time.time()

    logger.info(f"Starting transcription: {audio_path}")
    logger.info(f"  Model: {WHISPER_MODEL}, Compute: {WHISPER_COMPUTE_TYPE}, VAD: {VAD_FILTER}")

    with _job_lock:
        _job_status.active = True
        _job_status.progress = 0.0
        _job_status.message = "Transcribing..."
        _job_status.started_at = start_time

    try:
        # Run transcription — faster-whisper returns a generator + info tuple
        segments_gen, info = model.transcribe(
            audio_path,
            word_timestamps=True,
            vad_filter=VAD_FILTER,
        )

        total_duration = info.duration  # seconds
        logger.info(f"  Audio duration: {total_duration:.1f}s")

        with _job_lock:
            _job_status.message = f"Transcribing ({_format_duration(total_duration)} of audio)..."

        # Consume the generator, collecting segments and updating progress
        segments_list = []
        for segment in segments_gen:
            segments_list.append(segment)

            # Update progress based on how far through the audio we are
            if total_duration > 0:
                fraction = min(segment.end / total_duration, 1.0)
                with _job_lock:
                    _job_status.progress = round(fraction, 3)
                    elapsed_audio = _format_duration(segment.end)
                    total_str = _format_duration(total_duration)
                    pct = int(fraction * 100)
                    _job_status.message = (
                        f"Transcribing: {elapsed_audio} / {total_str} ({pct}%)"
                    )

        logger.info(f"  Produced {len(segments_list)} raw segments")

        # Group into sentences
        sentences = _group_segments_into_sentences(segments_list)
        logger.info(f"  Grouped into {len(sentences)} sentences")

        processing_time = time.time() - start_time
        logger.info(f"  Transcription complete in {processing_time:.1f}s")

        with _job_lock:
            _job_status.progress = 1.0
            _job_status.message = "Complete"
            _job_status.active = False

        return {
            "sentences": [asdict(s) for s in sentences],
            "duration_seconds": round(total_duration, 2),
            "model": WHISPER_MODEL,
            "processing_time_seconds": round(processing_time, 2),
        }

    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=True)
        with _job_lock:
            _job_status.active = False
            _job_status.progress = 0.0
            _job_status.message = f"Error: {str(e)}"
        raise


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


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BookSync Transcription Server",
    description="faster-whisper transcription API for Jetson Orin Nano",
    version="1.0.0",
)


@app.on_event("startup")
def startup_event():
    """Load the Whisper model into GPU memory when the server starts."""
    load_model()


@app.get("/v1/health")
def health():
    """Health check — returns model info and GPU status."""
    import torch

    gpu_available = torch.cuda.is_available()
    gpu_name = torch.cuda.get_device_name(0) if gpu_available else None

    return {
        "status": "healthy",
        "model": WHISPER_MODEL,
        "compute_type": WHISPER_COMPUTE_TYPE,
        "device": WHISPER_DEVICE,
        "vad_filter": VAD_FILTER,
        "gpu_available": gpu_available,
        "gpu_name": gpu_name,
        "model_loaded": model is not None,
    }


@app.get("/v1/status")
def get_status():
    """Get the progress of the current transcription job."""
    with _job_lock:
        return {
            "active": _job_status.active,
            "progress": _job_status.progress,
            "message": _job_status.message,
        }


@app.post("/v1/transcribe")
async def transcribe(audio_file: UploadFile = File(...)):
    """
    Transcribe an uploaded audio file.

    Accepts any audio format supported by ffmpeg (mp3, m4a, m4b, flac, wav, ogg, etc).
    Returns a list of sentences with start_ms and end_ms timestamps.

    Only one transcription can run at a time. If a transcription is already
    in progress, this endpoint returns HTTP 409.
    """
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    with _job_lock:
        if _job_status.active:
            raise HTTPException(
                status_code=409,
                detail="A transcription is already in progress. Check /v1/status for progress.",
            )

    # Save uploaded file to a temp location
    suffix = os.path.splitext(audio_file.filename or "audio.mp3")[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(audio_file.file, tmp)
        tmp.close()

        logger.info(f"Received file: {audio_file.filename} ({os.path.getsize(tmp.name)} bytes)")

        # Run transcription (synchronous — blocks this worker, which is fine
        # since we only have one worker and one job at a time)
        result = _transcribe_file(tmp.name)
        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Entry point (for running without uvicorn CLI)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT, workers=1)
