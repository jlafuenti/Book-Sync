"""
BookSync Transcription Server — faster-whisper on Jetson Orin Nano

A lightweight FastAPI server that accepts audio file uploads and returns
timestamped, sentence-segmented transcription results using faster-whisper.

Designed to run on a Jetson Orin Nano with GPU acceleration.

Features:
- Adaptive chunk sizing with automatic OoM recovery
- Checkpoint system for resuming interrupted transcriptions
- Preemptive memory management via CT2 model reload
"""

import asyncio
import gc
import hashlib
import json
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
# Adaptive chunking & memory management constants
# ---------------------------------------------------------------------------
DEFAULT_CHUNK_SIZE_SEC = 900        # 15-minute default chunk
MIN_CHUNK_SIZE_SEC = 112            # ~2-minute floor before giving up
SCALE_UP_AFTER_N_SUCCESSES = 3      # Successful small chunks before doubling back
PREEMPTIVE_RELOAD_THRESHOLD_MB = 1200  # Reload model if available memory below this
MAX_CHUNKS_BETWEEN_RELOADS = 15     # Force model reload after this many chunks
CHECKPOINT_DIR = "/tmp/booksync_checkpoints"

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

# Store recent transcription results for 24 hours to allow re-attachment
_recent_results = {}
_results_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
model = None


def load_model():
    """Load the faster-whisper model onto the GPU."""
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
# Memory management utilities
# ---------------------------------------------------------------------------

def _read_sys_mem_mb() -> dict:
    """Read MemAvailable and MemTotal from /proc/meminfo."""
    try:
        vals = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                key = key.strip()
                if key in ("MemTotal", "MemFree", "MemAvailable"):
                    vals[key] = int(rest.strip().split()[0]) // 1024  # kB -> MB
        return vals
    except Exception:
        return {}


def _reload_ct2_model() -> None:
    """
    Unload and reload the CTranslate2 model weights to flush its internal
    memory allocator. This is the only reliable way to reclaim GPU/unified
    memory between chunks on the Jetson (torch is not installed; ctranslate2
    4.x has no cache-flush API).
    """
    try:
        mem_before = _read_sys_mem_mb()
        logger.info(
            f"  [mem reload] Unloading CT2 model weights... "
            f"(sys avail={mem_before.get('MemAvailable', '?')}MB)"
        )
        t0 = time.time()
        model.model.unload_model()
        gc.collect()
        model.model.load_model()
        elapsed = time.time() - t0
        mem_after = _read_sys_mem_mb()
        recovered = mem_after.get("MemAvailable", 0) - mem_before.get("MemAvailable", 0)
        logger.info(
            f"  [mem reload] Model reloaded in {elapsed:.1f}s — "
            f"sys avail={mem_after.get('MemAvailable', '?')}MB "
            f"(+{recovered}MB recovered)"
        )
    except Exception as e:
        logger.warning(f"  [mem reload] CT2 model reload failed (non-fatal): {e}")


def _is_oom_error(exc: Exception) -> bool:
    """Check if an exception is an out-of-memory error."""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("out of memory", "oom", "cuda", "cudamalloc", "alloc"))


# ---------------------------------------------------------------------------
# Checkpoint system — preserves progress across OoM failures
# ---------------------------------------------------------------------------

def _checkpoint_key(audio_path: str, filename: str) -> str:
    """Generate a stable checkpoint key from original filename + file size."""
    file_size = os.path.getsize(audio_path)
    key = f"{filename}:{file_size}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _checkpoint_path(audio_path: str, filename: str) -> str:
    """Get the checkpoint file path for a given audio file."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, _checkpoint_key(audio_path, filename) + ".json")


def _save_checkpoint(
    ckpt_path: str,
    total_duration: float,
    completed_through_sec: int,
    current_chunk_size: int,
    sentences: List[TranscribedSentence],
) -> None:
    """Atomically save transcription progress to a checkpoint file."""
    data = {
        "version": 1,
        "total_duration": total_duration,
        "completed_through_sec": completed_through_sec,
        "current_chunk_size": current_chunk_size,
        "sentences": [asdict(s) for s in sentences],
        "saved_at": time.time(),
    }
    tmp_path = ckpt_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
    os.replace(tmp_path, ckpt_path)


def _load_checkpoint(ckpt_path: str) -> Optional[dict]:
    """Load a checkpoint file, returning None if not found or corrupt."""
    if not os.path.exists(ckpt_path):
        return None
    try:
        with open(ckpt_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"  Corrupt checkpoint file, starting fresh: {e}")
        try:
            os.unlink(ckpt_path)
        except OSError:
            pass
        return None


def _delete_checkpoint(ckpt_path: str) -> None:
    """Delete a checkpoint file after successful completion."""
    try:
        os.unlink(ckpt_path)
        logger.info(f"  Deleted checkpoint: {ckpt_path}")
    except OSError:
        pass


def _cleanup_old_checkpoints(max_age_hours: int = 48) -> None:
    """Remove checkpoint files older than max_age_hours."""
    if not os.path.isdir(CHECKPOINT_DIR):
        return
    now = time.time()
    max_age_sec = max_age_hours * 3600
    for name in os.listdir(CHECKPOINT_DIR):
        path = os.path.join(CHECKPOINT_DIR, name)
        try:
            if os.path.isfile(path) and (now - os.path.getmtime(path)) > max_age_sec:
                os.unlink(path)
                logger.info(f"  Cleaned up old checkpoint: {name}")
        except OSError:
            pass


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

    def _build_cmd(fast_seek: bool) -> list:
        base = ["ffmpeg", "-nostdin", "-threads", "0"]
        if fast_seek:
            base += ["-ss", str(start_sec), "-i", file]
        else:
            base += ["-i", file, "-ss", str(start_sec)]
        return base + ["-t", str(duration_sec), "-f", "s16le", "-ac", "1",
                       "-acodec", "pcm_s16le", "-ar", str(sr), "-"]

    try:
        out = subprocess.run(_build_cmd(fast_seek=True), capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        if start_sec == 0:
            logger.error(f"FFmpeg failed: {e.stderr.decode()}")
            raise RuntimeError(f"Failed to load audio chunk at {start_sec}s") from e
        logger.warning(f"FFmpeg fast seek failed at {start_sec}s, retrying with slow seek: {e.stderr.decode()[:200]}")
        try:
            out = subprocess.run(_build_cmd(fast_seek=False), capture_output=True, check=True).stdout
        except subprocess.CalledProcessError as e2:
            logger.error(f"FFmpeg slow seek also failed: {e2.stderr.decode()}")
            raise RuntimeError(f"Failed to load audio chunk at {start_sec}s") from e2

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
    except (subprocess.CalledProcessError, ValueError) as probe_err:
        # ffprobe could not read the duration (often a corrupt/truncated file).
        # Try mutagen as a best-effort fallback, but never let a missing optional
        # dependency or an unreadable file crash the request with a confusing
        # ModuleNotFoundError — surface a clear, decodable error instead.
        try:
            import mutagen
            fallback = mutagen.File(file)
            if fallback and fallback.info:
                return float(fallback.info.length)
        except Exception as fallback_err:
            logger.warning(f"Duration fallback (mutagen) failed for {file}: {fallback_err}")
        raise RuntimeError(
            f"Could not determine audio duration for {file} — the file is likely "
            f"corrupt or unreadable: {probe_err}"
        )


def _transcribe_file(audio_path: str, original_filename: str) -> dict:
    """
    Run faster-whisper on the given audio file with adaptive chunking.

    Features:
    - Adaptive chunk sizing: halves chunk size on OoM, scales back up after recovery
    - Checkpoint system: saves progress after each chunk for resume on failure
    - Preemptive memory management: reloads CT2 model when memory is low
    """
    global _job_status

    start_time = time.time()

    # Capture current_file before the job starts to avoid race conditions
    with _job_lock:
        captured_filename = _job_status.current_file

    logger.info(f"Starting transcription: {audio_path}")
    logger.info(f"  Model: {WHISPER_MODEL}, Compute: {WHISPER_COMPUTE_TYPE}, VAD: {VAD_FILTER}")
    mem = _read_sys_mem_mb()
    logger.info(
        f"  [mem start] sys total={mem.get('MemTotal', '?')}MB  "
        f"avail={mem.get('MemAvailable', '?')}MB  "
        f"free={mem.get('MemFree', '?')}MB"
    )

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

        # --- Check for checkpoint (resume from previous attempt) ---
        ckpt_path = _checkpoint_path(audio_path, original_filename)
        ckpt = _load_checkpoint(ckpt_path)

        if ckpt and abs(ckpt.get("total_duration", 0) - total_duration) < 1.0:
            all_sentences = [
                TranscribedSentence(**s) for s in ckpt.get("sentences", [])
            ]
            start_sec = ckpt["completed_through_sec"]
            current_chunk_size = ckpt.get("current_chunk_size", DEFAULT_CHUNK_SIZE_SEC)
            logger.info(
                f"  Resuming from checkpoint: {start_sec}s, "
                f"{len(all_sentences)} sentences already completed"
            )
        else:
            all_sentences = []
            start_sec = 0
            current_chunk_size = DEFAULT_CHUNK_SIZE_SEC

        chunk_idx = 0
        chunks_since_reload = 0
        consecutive_small_successes = 0

        # --- Adaptive chunk loop ---
        while start_sec < int(total_duration) + 1:
            # 1. Pre-chunk memory check
            mem = _read_sys_mem_mb()
            avail_mb = mem.get("MemAvailable", 9999)
            logger.info(
                f"  Processing chunk @ {start_sec}s (size={current_chunk_size}s): "
                f"avail={avail_mb}MB  free={mem.get('MemFree', '?')}MB"
            )

            # Preemptive model reload if memory is low
            if avail_mb < PREEMPTIVE_RELOAD_THRESHOLD_MB:
                logger.warning(
                    f"  Low memory ({avail_mb}MB < {PREEMPTIVE_RELOAD_THRESHOLD_MB}MB). "
                    f"Preemptive model reload."
                )
                _reload_ct2_model()
                chunks_since_reload = 0
            elif chunks_since_reload >= MAX_CHUNKS_BETWEEN_RELOADS:
                logger.info(f"  Scheduled model reload after {chunks_since_reload} chunks.")
                _reload_ct2_model()
                chunks_since_reload = 0

            # 2. Try to transcribe this chunk
            try:
                audio_array = load_audio_chunk(audio_path, start_sec, current_chunk_size)

                if len(audio_array) == 0:
                    logger.warning(f"  Chunk at {start_sec}s returned no audio data. Done.")
                    break

                segments_gen, info = model.transcribe(
                    audio_array,
                    vad_filter=VAD_FILTER,
                    condition_on_previous_text=False,
                )

                chunk_segments = []
                for segment in segments_gen:
                    chunk_segments.append(segment)

                    # Update progress
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

                # Group segments into sentences and offset timestamps
                chunk_sentences = _group_segments_into_sentences(chunk_segments)
                offset_ms = start_sec * 1000
                for s in chunk_sentences:
                    s.start_ms += offset_ms
                    s.end_ms += offset_ms
                    all_sentences.append(s)

                # Release references before next chunk
                del audio_array, segments_gen, info, chunk_segments
                gc.collect()

                # Advance position
                start_sec += current_chunk_size
                chunk_idx += 1
                chunks_since_reload += 1

                # Scale-up logic: after passing a problematic section, grow back
                if current_chunk_size < DEFAULT_CHUNK_SIZE_SEC:
                    consecutive_small_successes += 1
                    if consecutive_small_successes >= SCALE_UP_AFTER_N_SUCCESSES:
                        new_size = min(current_chunk_size * 2, DEFAULT_CHUNK_SIZE_SEC)
                        logger.info(
                            f"  {consecutive_small_successes} consecutive successes at "
                            f"{current_chunk_size}s — scaling up to {new_size}s"
                        )
                        current_chunk_size = new_size
                        consecutive_small_successes = 0
                else:
                    consecutive_small_successes = 0

                # Save checkpoint after every successful chunk
                _save_checkpoint(
                    ckpt_path, total_duration, start_sec,
                    current_chunk_size, all_sentences,
                )

                # Detect last chunk: if audio returned was shorter than expected
                expected_samples = current_chunk_size * 16000
                # (audio_array is deleted, so check via sentences/position instead)
                if start_sec >= total_duration:
                    break

            except Exception as e:
                if _is_oom_error(e):
                    logger.error(
                        f"  OoM at chunk {chunk_idx} ({start_sec}s, "
                        f"size={current_chunk_size}s): {e}"
                    )

                    # Reload model to recover memory
                    logger.info("  Reloading CT2 model after OoM to reset allocator state...")
                    _reload_ct2_model()
                    chunks_since_reload = 0
                    gc.collect()

                    # Halve chunk size and retry
                    current_chunk_size = current_chunk_size // 2
                    if current_chunk_size < MIN_CHUNK_SIZE_SEC:
                        logger.error(
                            f"  Chunk size {current_chunk_size}s below minimum "
                            f"({MIN_CHUNK_SIZE_SEC}s). Cannot recover."
                        )
                        raise
                    logger.info(
                        f"  Retrying @ {start_sec}s with reduced chunk size "
                        f"{current_chunk_size}s"
                    )
                    consecutive_small_successes = 0
                    # Do NOT advance start_sec — retry same position
                else:
                    raise

        # --- Success ---
        _delete_checkpoint(ckpt_path)

        processing_time = time.time() - start_time
        logger.info(f"  Transcription complete in {processing_time:.1f}s")
        logger.info(f"  Produced {len(all_sentences)} sentences overall")

        with _job_lock:
            _job_status.progress = 1.0
            _job_status.message = "Complete"
            _job_status.active = False

        result = {
            "sentences": [asdict(s) for s in all_sentences],
            "duration_seconds": round(total_duration, 2),
            "model": WHISPER_MODEL,
            "processing_time_seconds": round(processing_time, 2),
        }

        # Save result for later retrieval (in case client disconnected)
        with _results_lock:
            now = time.time()
            to_delete = [k for k, (v, t) in _recent_results.items() if now - t > 86400]
            for k in to_delete:
                del _recent_results[k]

            if captured_filename:
                _recent_results[captured_filename] = (result, now)
                logger.info(f"  Cached result for '{captured_filename}' (available via /v1/result/)")

        return result

    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=True)
        with _job_lock:
            _job_status.active = False
            _job_status.progress = 0.0
            _job_status.message = f"Error: {str(e)}"

        # Reload model to recover memory for next job.
        # Checkpoint is preserved so next attempt can resume.
        logger.info("  Reloading CT2 model after failure to reset allocator state...")
        _reload_ct2_model()
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
    _cleanup_old_checkpoints()


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
            "current_file": _job_status.current_file,
        }

@app.get("/v1/result/{filename}")
def get_result(filename: str):
    """Get the cached result of a completed transcription job."""
    with _results_lock:
        if filename in _recent_results:
            result, timestamp = _recent_results[filename]
            return JSONResponse(status_code=200, content=result)

    raise HTTPException(status_code=404, detail="Result not found or expired")


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
        result = await asyncio.to_thread(
            _transcribe_file, tmp.name, audio_file.filename
        )
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
