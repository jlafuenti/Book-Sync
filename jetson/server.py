"""
Tandem Transcription Server — faster-whisper on Jetson Orin Nano

A lightweight FastAPI server that accepts audio file uploads and returns
timestamped, sentence-segmented transcription results using faster-whisper.

Designed to run on a Jetson Orin Nano with GPU acceleration.

Features:
- Adaptive chunk sizing with automatic OoM recovery
- Checkpoint system for resuming interrupted transcriptions
- Preemptive memory management via CT2 model reload
- Lazy model load + idle unload, so the GPU isn't held while idle (issue #106)
- Cooperative pause at a chunk boundary, retaining the audio for a free resume
"""

import asyncio
import gc
import hashlib
import json
import os
import secrets
import time
import shutil
import logging
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from fastapi import Depends, FastAPI, Form, Header, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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

# Language code (ISO 639-1, e.g. "en") forced on every chunk. Unset = auto,
# which means "detect on the first chunk of a file and pin that answer for the
# rest of it" — never per-chunk re-detection (#246). A chunk that opens on
# music, silence or a foreign-language epigraph otherwise gets detected as
# another language and comes back as transliterated garbage for that whole
# 15-minute span, which alignment then silently interpolates across.
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "").strip() or None

# Largest request body accepted on POST /v1/transcribe, in bytes (#238). The
# upload costs roughly twice the file size in temp space — Starlette spools the
# multipart body to disk before the endpoint runs, and the endpoint copies it
# again — so an unbounded body fills the Jetson's disk. 0 disables the cap.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(4 * 1024 ** 3)))

# Free space (as a multiple of the declared body size) required before an upload
# is accepted: the spooled body plus the endpoint's copy.
UPLOAD_DISK_HEADROOM = 2

# Minutes of inactivity after which the Whisper weights (~4GB of unified memory
# on an Orin Nano 8GB) are released. The model reloads on the next job, which
# costs tens of seconds — irrelevant for multi-hour batch work, and it hands
# the memory back to whatever else shares this GPU. 0 disables unloading and
# restores the old always-resident behaviour.
MODEL_IDLE_UNLOAD_MIN = int(os.environ.get("MODEL_IDLE_UNLOAD_MIN", "30"))
IDLE_CHECK_INTERVAL_SEC = 60

# Shared secret required on every /v1/* request (Authorization: Bearer <key>).
# Anyone who can reach this port can submit transcription jobs and read cached
# results, so we refuse to start without one rather than silently running open.
TRANSCRIPTION_API_KEY = os.environ.get("TRANSCRIPTION_API_KEY", "")
if not TRANSCRIPTION_API_KEY:
    raise RuntimeError(
        "TRANSCRIPTION_API_KEY is unset — refusing to start. Generate a key in "
        "Tandem → Settings → Transcription → Remote Server API Key "
        "(click \"Generate Key\"), then copy that same value into "
        "TRANSCRIPTION_API_KEY here. (Or generate one yourself with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(32))\")."
    )

# ---------------------------------------------------------------------------
# Adaptive chunking & memory management constants
# ---------------------------------------------------------------------------
DEFAULT_CHUNK_SIZE_SEC = 900        # 15-minute default chunk
MIN_CHUNK_SIZE_SEC = 112            # ~2-minute floor before giving up
SCALE_UP_AFTER_N_SUCCESSES = 3      # Successful small chunks before doubling back
PREEMPTIVE_RELOAD_THRESHOLD_MB = 1200  # Reload model if available memory below this
MAX_CHUNKS_BETWEEN_RELOADS = 15     # Force model reload after this many chunks
CHECKPOINT_DIR = "/tmp/booksync_checkpoints"
OVERSIZED_CHUNK_TOLERANCE = 1.5     # ffmpeg returned this much more audio than requested -> reject

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("transcription-server")

# Unique per-process id, regenerated on every restart (including OoM-kills).
# Lets clients detect "the server restarted" vs. "the job actually finished"
# when /v1/status reports active=false.
INSTANCE_ID = uuid.uuid4().hex

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
    current_size: Optional[int] = None  # byte size the client declared
    language: Optional[str] = None      # pinned/configured Whisper language (#246)


class PausedAtCheckpoint(Exception):
    """Raised internally when the chunk loop stops on a pause request."""

    def __init__(self, completed_through_sec: int):
        super().__init__(f"Paused at {completed_through_sec}s")
        self.completed_through_sec = completed_through_sec


# Thread-safe global job status (only one job runs at a time)
_job_status = JobStatus()
_job_lock = threading.Lock()

# Set by POST /v1/pause. The chunk loop checks it between chunks and stops
# cleanly, leaving a checkpoint (and the audio) behind for a free resume.
_pause_event = threading.Event()

# Store recent transcription results for 24 hours to allow re-attachment
_recent_results = {}
_results_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Model loading / unloading
# ---------------------------------------------------------------------------
model = None

# "unloaded" | "loading" | "loaded" — reported by /v1/health so a client can
# tell "idle, weights released" apart from "broken".
_model_state = "unloaded"
_model_lock = threading.RLock()

# Wall-clock of the last transcription activity, for the idle-unload timer.
_last_activity = time.time()


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


def ensure_model_loaded() -> None:
    """
    Load the model if it isn't resident. Called at the start of every job
    rather than at startup, so an idle server holds no GPU memory (#106).
    """
    global _model_state
    with _model_lock:
        if model is not None:
            return
        _model_state = "loading"
        try:
            load_model()
        except Exception:
            _model_state = "unloaded"
            raise
        _model_state = "loaded"


def unload_model() -> bool:
    """
    Release the model weights and hand the memory back.

    Unlike :func:`_reload_ct2_model` (which flushes the allocator and
    immediately reloads), this drops the Python object too. Returns True if
    something was actually released.
    """
    global model, _model_state
    with _model_lock:
        if model is None:
            return False
        logger.info("Unloading Whisper model to release GPU memory...")
        mem_before = _read_sys_mem_mb()
        try:
            model.model.unload_model()
        except Exception as e:
            logger.warning(f"CT2 unload_model() failed (dropping the reference anyway): {e}")
        model = None
        _model_state = "unloaded"
        gc.collect()
        mem_after = _read_sys_mem_mb()
        logger.info(
            f"Model unloaded — sys avail={mem_after.get('MemAvailable', '?')}MB "
            f"(+{mem_after.get('MemAvailable', 0) - mem_before.get('MemAvailable', 0)}MB)"
        )
        return True


def _mark_activity() -> None:
    global _last_activity
    _last_activity = time.time()


def _should_unload_idle(now: float, last_activity: float, job_active: bool,
                        model_loaded: bool, idle_minutes: int) -> bool:
    """Pure decision half of the idle-unload timer, so it can be unit tested."""
    if idle_minutes <= 0:      # 0 = never unload (pre-#106 behaviour)
        return False
    if job_active or not model_loaded:
        return False
    return (now - last_activity) >= idle_minutes * 60


def maybe_unload_idle() -> bool:
    """Unload the model if it has been idle long enough. Returns True if it did."""
    with _job_lock:
        job_active = _job_status.active
    if not _should_unload_idle(
        time.time(), _last_activity, job_active, model is not None, MODEL_IDLE_UNLOAD_MIN
    ):
        return False
    logger.info(
        f"No transcription activity for {MODEL_IDLE_UNLOAD_MIN} minutes — "
        f"releasing the model."
    )
    return unload_model()


def _idle_unload_loop() -> None:  # pragma: no cover — thread plumbing
    while True:
        time.sleep(IDLE_CHECK_INTERVAL_SEC)
        try:
            maybe_unload_idle()
        except Exception as e:
            logger.warning(f"Idle-unload check failed: {e}")


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


class OversizedChunkError(RuntimeError):
    """Raised when ffmpeg returns far more audio than the requested chunk size.

    Seen with malformed/mis-concatenated source files where a timestamp
    discontinuity causes ffmpeg's `-t` to stop capping the output duration.
    Handled the same way as an OoM (shrink and retry) since feeding an
    unbounded array straight into faster-whisper is what actually OoMs the
    Jetson.
    """


def _is_chunk_oversized(actual_chunk_sec: float, requested_chunk_size: int) -> bool:
    """True if ffmpeg returned far more audio than requested (see OversizedChunkError)."""
    return actual_chunk_sec > requested_chunk_size * OVERSIZED_CHUNK_TOLERANCE


def _shrink_chunk_size(current_chunk_size: int) -> Optional[int]:
    """Halve the chunk size after an oversized-chunk or OoM failure.

    Shared by both recovery paths in `_transcribe_file`. Returns the halved
    size to retry with, or None once that size would drop below
    MIN_CHUNK_SIZE_SEC — the caller should treat None as a signal to stop
    retrying and propagate the original failure instead.
    """
    new_size = current_chunk_size // 2
    if new_size < MIN_CHUNK_SIZE_SEC:
        return None
    return new_size


# ---------------------------------------------------------------------------
# Checkpoint system — preserves progress across OoM failures
# ---------------------------------------------------------------------------

def _checkpoint_key_for(filename: str, file_size: int) -> str:
    """
    Stable checkpoint key from original filename + byte size.

    The client sends those two values rather than a hash, so this derivation
    lives here only and can change without a lockstep deploy of both sides.
    """
    return hashlib.sha256(f"{filename}:{file_size}".encode()).hexdigest()[:16]


def _checkpoint_key(audio_path: str, filename: str) -> str:
    """Checkpoint key for an audio file already on disk here."""
    return _checkpoint_key_for(filename, os.path.getsize(audio_path))


def _checkpoint_path_for(filename: str, file_size: int) -> str:
    """Checkpoint file path for a job identified by filename + size."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, _checkpoint_key_for(filename, file_size) + ".json")


def _checkpoint_path(audio_path: str, filename: str) -> str:
    """Get the checkpoint file path for a given audio file."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, _checkpoint_key(audio_path, filename) + ".json")


def _retained_audio_path(ckpt_path: str) -> str:
    """
    Where a paused job's audio is parked, beside its checkpoint.

    Keeping it means resuming costs one small JSON request instead of pushing
    the whole audiobook back over the network. The 48h checkpoint sweep
    reclaims the space if nothing ever resumes.
    """
    return ckpt_path[: -len(".json")] + ".audio"


def _save_checkpoint(
    ckpt_path: str,
    total_duration: float,
    completed_through_sec: int,
    current_chunk_size: int,
    sentences: List[TranscribedSentence],
    language: Optional[str] = None,
) -> None:
    """Atomically save transcription progress to a checkpoint file.

    `language` is the answer the first chunk detected (or the configured pin).
    Storing it is what lets a resumed job carry on in the same language rather
    than re-detecting from whatever the resume happens to start on (#246).
    """
    data = {
        "version": 1,
        "total_duration": total_duration,
        "completed_through_sec": completed_through_sec,
        "current_chunk_size": current_chunk_size,
        "sentences": [asdict(s) for s in sentences],
        "language": language,
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
    """Delete a checkpoint and any audio retained for it."""
    for path in (ckpt_path, _retained_audio_path(ckpt_path)):
        try:
            os.unlink(path)
            logger.info(f"  Deleted checkpoint file: {path}")
        except OSError:
            pass


def _retain_audio(ckpt_path: str, audio_path: str) -> bool:
    """
    Park the job's audio next to its checkpoint so a resume needs no upload.

    Moves rather than copies — the source is the request's temp upload, which
    is about to be deleted, and a multi-GB copy on the Orin is not free.
    ``shutil.move`` handles the cross-filesystem case. Returns True if the
    audio is in place afterwards.
    """
    retained = _retained_audio_path(ckpt_path)
    if os.path.abspath(audio_path) == os.path.abspath(retained):
        return True  # already resuming from the retained copy
    try:
        shutil.move(audio_path, retained)
        logger.info(f"  Retained audio for resume: {retained}")
        return True
    except OSError as e:
        logger.warning(f"  Could not retain audio for resume (will need re-upload): {e}")
        return False


def _upload_tmp_is_on_the_checkpoint_volume() -> bool:
    """True when TMPDIR sits inside CHECKPOINT_DIR, as the compose template
    configures it (#238) — i.e. when the upload temp dir is ours to sweep."""
    tmp = os.path.abspath(tempfile.gettempdir())
    ckpt = os.path.abspath(CHECKPOINT_DIR)
    return tmp == ckpt or tmp.startswith(ckpt + os.sep)


def _cleanup_stale_uploads(max_age_hours: int = 48) -> None:
    """
    Remove abandoned upload temp files from the checkpoint volume.

    The endpoint deletes its own temp file, so these only appear after a hard
    stop mid-upload (an OoM-kill, a power cut). One left behind is multiple GB
    on the volume the free-space guard measures, which then refuses every later
    job — the disk cap defeating itself.

    Deliberately a no-op unless TMPDIR is inside CHECKPOINT_DIR: on a dev box
    or in CI, `tempfile.gettempdir()` is the system temp directory shared with
    every other process, and sweeping that would delete other people's files.
    """
    if not _upload_tmp_is_on_the_checkpoint_volume():
        return
    upload_tmp = tempfile.gettempdir()
    if not os.path.isdir(upload_tmp):
        return
    cutoff = time.time() - max_age_hours * 3600
    for name in os.listdir(upload_tmp):
        path = os.path.join(upload_tmp, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.unlink(path)
                logger.info(f"  Cleaned up abandoned upload temp file: {name}")
        except OSError:
            pass


def _cleanup_old_checkpoints(max_age_hours: int = 48) -> None:
    """Remove checkpoint files — and any retained audio — older than max_age_hours."""
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


def _transcribe_file(
    audio_path: str,
    original_filename: str,
    language: Optional[str] = None,
) -> dict:
    """
    Run faster-whisper on the given audio file with adaptive chunking.

    Features:
    - Adaptive chunk sizing: halves chunk size on OoM, scales back up after recovery
    - Checkpoint system: saves progress after each chunk for resume on failure
    - Preemptive memory management: reloads CT2 model when memory is low
    - One language per file: `language` (per job) beats WHISPER_LANGUAGE (per
      worker) beats the checkpoint's pin beats detecting once on chunk 1 (#246)
    """
    global _job_status

    start_time = time.time()

    # The filename is an argument, deliberately not `_job_status.current_file`:
    # a second request that claimed the slot after this one started used to
    # overwrite that global, and the finished transcript got cached under the
    # other job's name (#236).
    captured_filename = original_filename

    # An explicit per-job language wins over the worker-wide default; a
    # checkpoint's pin (read below) fills in when neither is set.
    pinned_language = language or WHISPER_LANGUAGE

    logger.info(f"Starting transcription: {audio_path}")
    logger.info(f"  Model: {WHISPER_MODEL}, Compute: {WHISPER_COMPUTE_TYPE}, VAD: {VAD_FILTER}")
    logger.info(f"  Language: {pinned_language or 'auto (detect once, then pin)'}")
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
        _job_status.language = pinned_language

    # A pause request that arrived while nothing was running must not stop the
    # job we're about to start.
    _pause_event.clear()
    _mark_activity()

    try:
        # Load on demand — an idle server holds no GPU memory (#106). The first
        # job after an unload pays tens of seconds, which is nothing against a
        # multi-hour transcription.
        if model is None:
            with _job_lock:
                _job_status.message = "Loading model..."
        ensure_model_loaded()

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
            # Carry the language the paused half was transcribed in, unless
            # this job was given one explicitly.
            if not pinned_language and ckpt.get("language"):
                pinned_language = ckpt["language"]
                with _job_lock:
                    _job_status.language = pinned_language
                logger.info(f"  Resuming in the checkpointed language: {pinned_language}")
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
            # 0. Pause request? A chunk boundary is the only lossless place to
            # stop: everything before it is already in the checkpoint, and the
            # chunk we'd start now would be thrown away.
            if _pause_event.is_set():
                _save_checkpoint(
                    ckpt_path, total_duration, start_sec,
                    current_chunk_size, all_sentences,
                    language=pinned_language,
                )
                raise PausedAtCheckpoint(start_sec)

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

                actual_chunk_sec = len(audio_array) / 16000
                if _is_chunk_oversized(actual_chunk_sec, current_chunk_size):
                    del audio_array
                    raise OversizedChunkError(
                        f"ffmpeg returned {actual_chunk_sec:.1f}s of audio for a "
                        f"{current_chunk_size}s request at {start_sec}s — likely a "
                        f"timestamp discontinuity in a malformed/corrupt source file"
                    )

                segments_gen, info = model.transcribe(
                    audio_array,
                    vad_filter=VAD_FILTER,
                    condition_on_previous_text=False,
                    language=pinned_language,
                )

                # Detect once, then pin. `info` is populated as soon as
                # transcribe() returns (the segments themselves are lazy), so
                # read the answer here, before `info` is released below.
                if not pinned_language:
                    detected = getattr(info, "language", None)
                    if detected:
                        pinned_language = detected
                        logger.info(
                            f"  Detected language '{detected}' — pinning it for the "
                            f"rest of this file"
                        )
                        with _job_lock:
                            _job_status.language = pinned_language

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
                    language=pinned_language,
                )

                # Detect last chunk: if audio returned was shorter than expected
                expected_samples = current_chunk_size * 16000
                # (audio_array is deleted, so check via sentences/position instead)
                if start_sec >= total_duration:
                    break

            except OversizedChunkError as e:
                logger.error(f"  Oversized chunk at {start_sec}s: {e}")

                # No transcribe() call happened, so there's nothing to reload —
                # just shrink and retry at the same position.
                new_size = _shrink_chunk_size(current_chunk_size)
                if new_size is None:
                    logger.error(
                        f"  Chunk size below minimum ({MIN_CHUNK_SIZE_SEC}s) and "
                        f"ffmpeg is still returning oversized audio at {start_sec}s. "
                        f"Aborting — source file is likely corrupt/malformed and "
                        f"needs to be re-imported."
                    )
                    raise
                current_chunk_size = new_size
                logger.info(
                    f"  Retrying @ {start_sec}s with reduced chunk size "
                    f"{current_chunk_size}s"
                )
                consecutive_small_successes = 0
                # Do NOT advance start_sec — retry same position

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
                    new_size = _shrink_chunk_size(current_chunk_size)
                    if new_size is None:
                        logger.error(
                            f"  Chunk size below minimum ({MIN_CHUNK_SIZE_SEC}s). "
                            f"Cannot recover."
                        )
                        raise
                    current_chunk_size = new_size
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
        _mark_activity()

        result = {
            "sentences": [asdict(s) for s in all_sentences],
            "duration_seconds": round(total_duration, 2),
            "model": WHISPER_MODEL,
            # The language every chunk of this file was transcribed in —
            # configured, or detected on chunk 1 and pinned from there (#246).
            "language": pinned_language,
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

    except PausedAtCheckpoint as paused:
        # Not a failure. Progress through `completed_through_sec` is on disk and
        # the audio is parked beside it, so the client can resume with a single
        # small request once its window reopens.
        audio_retained = _retain_audio(ckpt_path, audio_path)
        fraction = (
            min(paused.completed_through_sec / total_duration, 1.0)
            if total_duration else 0.0
        )
        logger.info(
            f"Paused {original_filename} at {paused.completed_through_sec}s of "
            f"{total_duration:.0f}s ({len(all_sentences)} sentences banked)."
        )

        with _job_lock:
            _job_status.active = False
            _job_status.progress = round(fraction, 3)
            _job_status.message = (
                f"Paused at {_format_duration(paused.completed_through_sec)}"
            )
        _pause_event.clear()
        _mark_activity()

        # Free the GPU immediately — releasing it is the entire point of pausing.
        unload_model()

        # Deliberately NOT cached in _recent_results: a partial transcript
        # served from /v1/result would be indistinguishable from a finished one
        # and would silently truncate the book.
        return {
            "status": "paused",
            "completed_through_sec": paused.completed_through_sec,
            "progress": round(fraction, 3),
            "sentences_so_far": len(all_sentences),
            "duration_seconds": round(total_duration, 2),
            "audio_retained": audio_retained,
            "language": pinned_language,
        }

    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=True)
        with _job_lock:
            _job_status.active = False
            _job_status.progress = 0.0
            _job_status.message = f"Error: {str(e)}"
        _mark_activity()

        # Reload model to recover memory for next job.
        # Checkpoint is preserved so next attempt can resume.
        if model is not None:
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

app = FastAPI(
    title="Tandem Transcription Server",
    description="faster-whisper transcription API for Jetson Orin Nano",
    version="1.0.0",
)

# No CORS middleware: the browser never talks to this server directly. The
# Tandem web app's "Test Connection" button proxies through the main
# server's /api/settings/test-remote, and the transcription pipeline itself
# is server-to-server httpx (not subject to CORS).


async def _send_json_response(send, status_code: int, payload: dict) -> None:
    """Emit a complete JSON response straight onto the ASGI channel."""
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class UploadLimitMiddleware:
    """
    Bound the size of a `POST /v1/transcribe` body before it is parsed (#238).

    This has to be pure ASGI, outside the FastAPI app: Starlette parses the
    whole multipart body into a spooled temp file *before* the endpoint
    function runs, so a check inside the endpoint is far too late to stop a
    huge upload from filling the disk. The peak cost is roughly twice the file
    size — the spooled body plus the endpoint's own copy — which is why the
    free-space check asks for `UPLOAD_DISK_HEADROOM x` the declared length.

    Three guards:
      * `Content-Length` over `MAX_UPLOAD_BYTES` -> 413, nothing read.
      * not enough free space in the temp dir      -> 507, nothing read.
      * a body with no length that runs over       -> 413, cut off mid-stream.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v1/transcribe"
        ):
            await self.app(scope, receive, send)
            return

        cap = MAX_UPLOAD_BYTES
        declared = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                break

        if declared is not None:
            if cap > 0 and declared > cap:
                logger.warning(
                    f"413: upload of {declared} bytes exceeds MAX_UPLOAD_BYTES ({cap})"
                )
                await _send_json_response(send, 413, {
                    "detail": (
                        f"Upload is larger than this worker accepts "
                        f"({declared} bytes > MAX_UPLOAD_BYTES={cap}). Raise "
                        f"MAX_UPLOAD_BYTES if the file is legitimate."
                    ),
                })
                return

            needed = declared * UPLOAD_DISK_HEADROOM
            try:
                free = shutil.disk_usage(tempfile.gettempdir()).free
            except OSError:
                free = None
            if free is not None and free < needed:
                logger.error(
                    f"507: {free} bytes free in {tempfile.gettempdir()}, need "
                    f"{needed} for a {declared}-byte upload"
                )
                await _send_json_response(send, 507, {
                    "detail": (
                        f"Worker is out of disk: {free} bytes free where "
                        f"{needed} are needed for a {declared}-byte upload. "
                        f"Free {CHECKPOINT_DIR} or grow the volume behind it."
                    ),
                })
                return

        # No Content-Length (or a chunked body): count as it arrives and cut it
        # off at the cap. Truncating the stream makes the multipart parse fail
        # inside the app; we discard whatever it answers and send the 413.
        state = {"read": 0, "over": False}

        async def counting_receive():
            if state["over"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                state["read"] += len(message.get("body") or b"")
                if cap > 0 and state["read"] > cap:
                    state["over"] = True
                    logger.warning(
                        f"413: streamed upload passed MAX_UPLOAD_BYTES ({cap}) — cutting it off"
                    )
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message):
            if state["over"]:
                return  # the app's answer is moot — ours is the 413 below
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not state["over"]:
                raise
        if state["over"]:
            await _send_json_response(send, 413, {
                "detail": (
                    f"Upload exceeded MAX_UPLOAD_BYTES ({cap} bytes) and was "
                    f"cut off. Raise MAX_UPLOAD_BYTES if the file is legitimate."
                ),
            })


app.add_middleware(UploadLimitMiddleware)


def verify_api_key(authorization: str = Header(default="")) -> None:
    """Require `Authorization: Bearer <TRANSCRIPTION_API_KEY>` on every /v1/* route."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, TRANSCRIPTION_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.on_event("startup")
def startup_event():
    """
    Prepare to serve. The model is deliberately *not* loaded here (#106) —
    it loads on the first job and is released again after
    MODEL_IDLE_UNLOAD_MIN, so an idle server leaves the GPU to whatever else
    shares it.
    """
    # TMPDIR points at the sized checkpoint volume in the compose template
    # (#238), so both the spooled request body and the endpoint's copy land on
    # storage the operator was told to size — not the container's default /tmp.
    # Compose can't create the directory, so do it here.
    upload_tmp = tempfile.gettempdir()
    try:
        os.makedirs(upload_tmp, exist_ok=True)
        logger.info(f"Upload temp dir: {upload_tmp} (cap {MAX_UPLOAD_BYTES} bytes)")
    except OSError as e:
        logger.warning(f"Could not create the upload temp dir {upload_tmp}: {e}")

    _cleanup_stale_uploads()
    _cleanup_old_checkpoints()
    if MODEL_IDLE_UNLOAD_MIN > 0:
        threading.Thread(target=_idle_unload_loop, daemon=True).start()
        logger.info(
            f"Model will be loaded on demand and released after "
            f"{MODEL_IDLE_UNLOAD_MIN} idle minutes."
        )
    else:
        logger.info("MODEL_IDLE_UNLOAD_MIN=0 — model stays resident once loaded.")


@app.get("/v1/health", dependencies=[Depends(verify_api_key)])
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
        # An unloaded model is the normal idle state since #106, not a fault —
        # `status` is what tells you whether this server is usable.
        "model_loaded": model is not None,
        "model_state": _model_state,
        "idle_unload_minutes": MODEL_IDLE_UNLOAD_MIN,
    }


@app.get("/v1/status", dependencies=[Depends(verify_api_key)])
def get_status():
    """Get the progress of the current transcription job."""
    with _job_lock:
        return {
            "active": _job_status.active,
            "progress": _job_status.progress,
            "message": _job_status.message,
            "current_file": _job_status.current_file,
            # Configured, or detected on the first chunk and pinned (#246).
            "language": _job_status.language,
            "instance_id": INSTANCE_ID,
        }

def _try_claim_job(
    filename: Optional[str],
    size: Optional[int] = None,
    language: Optional[str] = None,
) -> bool:
    """
    Take the single job slot, or report that someone else already has it.

    Check and claim happen under one `_job_lock` acquisition (#236). Splitting
    them — as the old "is anything active?" check did — let two requests whose
    bodies landed within a few seconds of each other both pass the guard and
    both start a job: two faster-whisper processes is an OoM on an 8 GB Orin,
    and the second one's `current_file` overwrote the first's, filing the first
    job's transcript under the wrong name.
    """
    with _job_lock:
        if _job_status.active:
            return False
        _job_status.active = True
        _job_status.progress = 0.0
        _job_status.message = "Queued"
        _job_status.started_at = time.time()
        _job_status.current_file = filename
        _job_status.current_size = size
        _job_status.language = language
        return True


def _release_job() -> None:
    """Hand the slot back after a failure between the claim and the handoff.

    `_transcribe_file` clears `active` itself on all three of its exit paths,
    so this is only for the window before it is entered (a failed copy, say) —
    otherwise a wedged slot would reject every later request with a 409.
    """
    with _job_lock:
        _job_status.active = False
        _job_status.progress = 0.0
        _job_status.message = "Idle"
        _job_status.started_at = None


def _active_job_conflict(requested_file: str, client_ip: str = "unknown"):
    """
    Build the 409 JSONResponse describing the job that holds the slot.

    Shared by /v1/transcribe and /v1/transcribe/resume — only one job may run
    at a time, and the body tells the client exactly what is holding the slot
    so it can decide between waiting and re-attaching. Called only after
    `_try_claim_job` has already refused, so the answer is never None.
    """
    with _job_lock:
        running_for = ""
        if _job_status.started_at:
            elapsed = time.time() - _job_status.started_at
            hours = int(elapsed) // 3600
            mins = (int(elapsed) % 3600) // 60
            running_for = f"{hours}h {mins}m" if hours else f"{mins}m"

        current_file = _job_status.current_file
        progress = _job_status.progress
        message = _job_status.message

    logger.warning(
        f"409 Conflict: Rejected transcribe request for '{requested_file}' "
        f"from {client_ip}. Currently transcribing '{current_file}' "
        f"({message}, running for {running_for})."
    )
    return JSONResponse(
        status_code=409,
        content={
            "detail": "A transcription is already in progress.",
            "instance_id": INSTANCE_ID,
            "current_job": {
                "file": current_file,
                "progress": progress,
                "message": message,
                "running_for": running_for,
            },
        },
    )


@app.get("/v1/result/{filename}", dependencies=[Depends(verify_api_key)])
def get_result(filename: str):
    """
    Get the cached result of a **completed** transcription job.

    Paused jobs are never cached here — a partial transcript served from this
    route is indistinguishable from a finished one and would silently truncate
    the book. Use /v1/checkpoint to ask about partial progress.
    """
    with _results_lock:
        if filename in _recent_results:
            result, timestamp = _recent_results[filename]
            return JSONResponse(status_code=200, content=result)

    raise HTTPException(status_code=404, detail="Result not found or expired")


# ---------------------------------------------------------------------------
# Off-hours control surface (issue #106)
# ---------------------------------------------------------------------------

class ResumeRequest(BaseModel):
    """Identity of a paused job: the original filename and its byte size.

    `language` overrides the worker default for the resumed run; leaving it
    unset keeps whatever the checkpoint pinned (#246).
    """

    filename: str
    size: int
    language: Optional[str] = None


@app.post("/v1/pause", dependencies=[Depends(verify_api_key)])
def request_pause():
    """
    Ask the running job to stop at its next chunk boundary.

    Idempotent, and a no-op when nothing is running — the flag is cleared at
    the start of every job so it can't leak into the next one.
    """
    with _job_lock:
        active = _job_status.active
        current_file = _job_status.current_file

    if not active:
        return {"paused_requested": False, "detail": "No transcription is running."}

    _pause_event.set()
    logger.info(f"Pause requested for '{current_file}' — will stop at the next chunk.")
    return {"paused_requested": True, "current_file": current_file}


@app.post("/v1/unload", dependencies=[Depends(verify_api_key)])
def unload():
    """Release the model weights now. Refuses while a job is running."""
    with _job_lock:
        if _job_status.active:
            return {
                "unloaded": False,
                "model_state": _model_state,
                "detail": "A transcription is running — pause it first.",
            }

    unloaded = unload_model()
    return {"unloaded": unloaded, "model_state": _model_state}


@app.get("/v1/checkpoint", dependencies=[Depends(verify_api_key)])
def get_checkpoint(filename: str, size: int):
    """Report any saved partial progress for a job, and whether its audio is here."""
    ckpt_path = _checkpoint_path_for(filename, size)
    ckpt = _load_checkpoint(ckpt_path)
    if not ckpt:
        return {"exists": False}

    total = ckpt.get("total_duration") or 0
    completed = ckpt.get("completed_through_sec", 0)
    return {
        "exists": True,
        "completed_through_sec": completed,
        "progress": round(min(completed / total, 1.0), 3) if total else 0.0,
        "sentences": len(ckpt.get("sentences", [])),
        "audio_retained": os.path.exists(_retained_audio_path(ckpt_path)),
    }


@app.delete("/v1/checkpoint", dependencies=[Depends(verify_api_key)])
def delete_checkpoint(filename: str, size: int):
    """Drop a job's saved progress and retained audio (the client cancelled it)."""
    ckpt_path = _checkpoint_path_for(filename, size)
    existed = os.path.exists(ckpt_path) or os.path.exists(_retained_audio_path(ckpt_path))
    _delete_checkpoint(ckpt_path)
    return {"deleted": existed}


@app.post("/v1/transcribe/resume", dependencies=[Depends(verify_api_key)])
async def transcribe_resume(body: ResumeRequest):
    """
    Continue a paused job from the audio retained here — no re-upload.

    404 means the audio is gone (swept, or never retained); the client should
    fall back to POST /v1/transcribe, which still picks the checkpoint up.
    """
    ckpt_path = _checkpoint_path_for(body.filename, body.size)
    audio_path = _retained_audio_path(ckpt_path)
    if not os.path.exists(ckpt_path) or not os.path.exists(audio_path):
        raise HTTPException(
            status_code=404,
            detail="No retained audio for this file — upload it again.",
        )

    # Check-and-claim in one lock acquisition (#236) — the resume path starts
    # the same single job slot the upload path does.
    if not _try_claim_job(body.filename, body.size, body.language):
        return _active_job_conflict(body.filename)

    logger.info(f"Resuming {body.filename} from retained audio at {audio_path}")
    handed_off = False
    try:
        # Note: no temp-file cleanup here. The audio belongs to the checkpoint
        # and is removed by _delete_checkpoint on completion (or by the 48h
        # sweep if this job never finishes).
        handed_off = True
        result = await asyncio.to_thread(
            _transcribe_file, audio_path, body.filename, body.language
        )
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # _transcribe_file clears the slot on every one of its exit paths; this
        # only covers a failure before it was ever entered.
        if not handed_off:
            _release_job()


@app.post("/v1/transcribe", dependencies=[Depends(verify_api_key)])
async def transcribe(
    request: Request,
    audio_file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
):
    """
    Transcribe an uploaded audio file.

    Accepts any audio format supported by ffmpeg (mp3, m4a, m4b, flac, wav, ogg, etc).
    Returns a list of sentences with start_ms and end_ms timestamps.

    `language` (optional form field) forces an ISO 639-1 language code for this
    job, overriding the worker's WHISPER_LANGUAGE; omitting it detects once on
    the first chunk and pins that for the rest of the file (#246).

    Only one transcription can run at a time. If a transcription is already
    in progress, this endpoint returns HTTP 409.
    """

    # The model is loaded on demand inside _transcribe_file (#106): an unloaded
    # model is the idle resting state, not a reason to reject work.
    client_ip = request.client.host if request and request.client else "unknown"
    job_language = (language or "").strip() or None

    # Check and claim in one lock acquisition (#236). Everything below runs
    # with the slot already held, so a second request that arrives during the
    # copy gets a 409 rather than starting a second job.
    if not _try_claim_job(audio_file.filename, None, job_language):
        return _active_job_conflict(audio_file.filename, client_ip)

    # Save uploaded file to a temp location
    suffix = os.path.splitext(audio_file.filename or "audio.mp3")[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handed_off = False
    try:
        shutil.copyfileobj(audio_file.file, tmp)
        tmp.close()

        file_size = os.path.getsize(tmp.name)
        with _job_lock:
            _job_status.current_size = file_size
        logger.info(
            f"Received file: {audio_file.filename} ({file_size / (1024 * 1024):.1f} MB) "
            f"from {client_ip}"
        )

        # Run transcription in a background thread so the event loop
        # stays free for /v1/status polling requests
        handed_off = True
        result = await asyncio.to_thread(
            _transcribe_file, tmp.name, audio_file.filename, job_language
        )
        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # A failure before the handoff (a failed copy, a full disk) would
        # otherwise leave the slot claimed and 409 every later request.
        if not handed_off:
            _release_job()
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
