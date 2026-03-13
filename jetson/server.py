"""
BookSync Transcription Server — faster-whisper on Jetson Orin Nano

A lightweight FastAPI server that accepts audio file uploads and returns
timestamped, sentence-segmented transcription results using faster-whisper.

Designed to run on a Jetson Orin Nano with GPU acceleration.
"""

import asyncio
import os
import time
import shutil
import logging
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
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
    current_file: Optional[str] = None  # filename being transcribed


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

def _get_audio_duration(file: str) -> float:
    """Get the duration of the audio file using ffprobe."""
    import subprocess
    cmd = [
        "ffprobe", 
        "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        file
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError):
        # Fallback to mutagen if ffprobe fails
        import mutagen
        fallback = mutagen.File(file)
        if fallback and fallback.info:
            return float(fallback.info.length)
        return 0.0

def _transcribe_file(audio_path: str) -> dict:
    """
    Run faster-whisper on the given audio file using chunking to avoid OOM.
    Updates _job_status with progress.
    """
    global _job_status

    start_time = time.time()

    logger.info(f"Starting transcription: {audio_path}")
    logger.info(f"  Model: {WHISPER_MODEL}, Compute: {WHISPER_COMPUTE_TYPE}, VAD: {VAD_FILTER}")

    with _job_lock:
        _job_status.active = True
        _job_status.progress = 0.0
        _job_status.message = "Initializing..."
        _job_status.started_at = start_time

    try:
        total_duration = _get_audio_duration(audio_path)
        logger.info(f"  Audio duration: {total_duration:.1f}s")
        
        with _job_lock:
            _job_status.message = f"Transcribing ({_format_duration(total_duration)} of audio)..."

        CHUNK_SIZE_SEC = 3600  # 1 hour chunks
        all_sentences = []
        
        for start_sec in range(0, int(total_duration) + 1, CHUNK_SIZE_SEC):
            logger.info(f"  Processing chunk {start_sec}s - {start_sec + CHUNK_SIZE_SEC}s")
            audio_array = load_audio_chunk(audio_path, start_sec, CHUNK_SIZE_SEC)
            
            if len(audio_array) == 0:
                logger.warning(f"  Chunk at {start_sec}s returned no audio data. Skipping.")
                continue

            segments_gen, info = model.transcribe(
                audio_array,
                word_timestamps=True,
                vad_filter=VAD_FILTER,
            )

            chunk_segments = []
            for segment in segments_gen:
                # Add the segment to our chunk list
                chunk_segments.append(segment)

                # Overall progress = (start_sec + segment.end) / total_duration
                if total_duration > 0:
                    overall_time = min(start_sec + segment.end, total_duration)
                    fraction = min(overall_time / total_duration, 1.0)
                    with _job_lock:
                        _job_status.progress = round(fraction, 3)
                        elapsed_audio = _format_duration(overall_time)
                        total_str = _format_duration(total_duration)
                        pct = int(fraction * 100)
                        _job_status.message = (
                            f"Transcribing: {elapsed_audio} / {total_str} ({pct}%)"
                        )
            
            # Group the chunk's segments into sentences
            chunk_sentences = _group_segments_into_sentences(chunk_segments)
            
            # Offset the timestamps by the chunk's start time
            offset_ms = start_sec * 1000
            for s in chunk_sentences:
                s.start_ms += offset_ms
                s.end_ms += offset_ms
                all_sentences.append(s)

        processing_time = time.time() - start_time
        logger.info(f"  Transcription complete in {processing_time:.1f}s")
        logger.info(f"  Produced {len(all_sentences)} sentences overall")

        with _job_lock:
            _job_status.progress = 1.0
            _job_status.message = "Complete"
            _job_status.active = False

        return {
            "sentences": [asdict(s) for s in all_sentences],
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

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="BookSync Transcription Server",
    description="faster-whisper transcription API for Jetson Orin Nano",
    version="1.0.0",
)

# Enable CORS for the frontend "Test Connection" button
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    """Load the Whisper model into GPU memory when the server starts."""
    load_model()


@app.get("/v1/health")
def health():
    """Health check — returns model info and GPU status."""
    gpu_available = False
    gpu_name = None

    try:
        import torch
        gpu_available = torch.cuda.is_available()
        gpu_name = torch.cuda.get_device_name(0) if gpu_available else None
    except ImportError:
        # If torch is missing (e.g. strict CTranslate2 image), 
        # assume CUDA is available if nvcc or standard Jetson paths exist
        gpu_available = os.path.exists("/dev/nvhost-gpu") or os.path.exists("/usr/local/cuda")
        gpu_name = "Jetson GPU (CTranslate2 Mode)" if gpu_available else None

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
async def transcribe(request: Request, audio_file: UploadFile = File(...)):
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
            running_for = ""
            if _job_status.started_at:
                elapsed = time.time() - _job_status.started_at
                hours = int(elapsed) // 3600
                mins = (int(elapsed) % 3600) // 60
                running_for = f"{hours}h {mins}m" if hours else f"{mins}m"

            client_ip = request.client.host if request and request.client else "unknown"
            logger.warning(
                f"409 Conflict: Rejected transcribe request for '{audio_file.filename}' "
                f"from {client_ip}. Currently transcribing '{_job_status.current_file}' "
                f"({_job_status.message}, running for {running_for})."
            )
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "A transcription is already in progress.",
                    "current_job": {
                        "file": _job_status.current_file,
                        "progress": _job_status.progress,
                        "message": _job_status.message,
                        "running_for": running_for,
                    },
                },
            )

    # Save uploaded file to a temp location
    suffix = os.path.splitext(audio_file.filename or "audio.mp3")[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(audio_file.file, tmp)
        tmp.close()

        client_ip = request.client.host if request and request.client else "unknown"
        file_size_mb = os.path.getsize(tmp.name) / (1024 * 1024)
        logger.info(
            f"Received file: {audio_file.filename} ({file_size_mb:.1f} MB) "
            f"from {client_ip}"
        )

        # Track current filename for 409 details
        with _job_lock:
            _job_status.current_file = audio_file.filename

        # Run transcription in a background thread so the event loop
        # stays free for /v1/status polling requests
        result = await asyncio.to_thread(_transcribe_file, tmp.name)
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
# Suppress noisy /v1/status access log lines
# ---------------------------------------------------------------------------
class StatusEndpointFilter(logging.Filter):
    """Filter out GET /v1/status 200 from uvicorn access logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if 'GET /v1/status' in msg and '200' in msg:
            return False
        return True

# Apply filter to uvicorn access logger
logging.getLogger("uvicorn.access").addFilter(StatusEndpointFilter())


# ---------------------------------------------------------------------------
# Entry point (for running without uvicorn CLI)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT, workers=1)
