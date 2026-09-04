"""
Remote Whisper Transcription Provider (stub)

Uploads audio to a remote faster-whisper server (e.g. Jetson Orin Nano)
and returns the results. Full implementation in Task 4.
"""

import logging
from typing import List, Optional, Callable

import os
import asyncio
import logging
import time
from typing import List, Optional, Callable

from services.transcription_providers.base import (
    TranscriptionProvider,
    ProviderUnavailableError,
    TranscriptionError,
    TranscriptionPaused,
)
from services.transcription import TranscribedSentence

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bounds on the two "poll the worker until it says the right thing" loops
# (issue #195)
#
# Both loops used to be `while True:` with every transport error swallowed at
# DEBUG. If the worker died — or stayed busy forever — while we were inside
# one, the queue item stayed `in_progress` with no error recorded, and because
# the queue is strictly serial nothing else was ever dispatched. Only a restart
# cleared it, and `reset_stale_items` then walked straight back in.
#
# Each loop now has two exits: a wall-clock deadline (`self.timeout`, the same
# budget the blocking upload gets) and a consecutive-transport-error cap. Both
# raise ProviderUnavailableError so the queue's retry ladder takes over.
#
# The caps are deliberately generous enough to ride out a poll blip: the tests
# pin both the giving-up and the surviving-a-blip halves.
# ---------------------------------------------------------------------------
REATTACH_POLL_INTERVAL_SEC = 10
REATTACH_MAX_POLL_ERRORS = 6            # ~1 min unreachable at 10 s

WAIT_FOR_IDLE_POLL_INTERVAL_SEC = 30
WAIT_FOR_IDLE_MAX_POLL_ERRORS = 10      # ~5 min unreachable at 30 s


class RemoteWhisperProvider(TranscriptionProvider):
    """
    Sends audio to a remote faster-whisper HTTP API for transcription.
    Intended for use with the Jetson Orin Nano transcription server.
    """

    def __init__(self, remote_url: str, timeout: int = 7200, api_key: str = ""):
        self.remote_url = remote_url.rstrip("/")
        self.timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def _poll_progress(self, stop_event: asyncio.Event, progress_callback: Callable):
        """Polls the remote server for progress while transcription is running.

        Polling stays at 2 Hz so a pause or a stall is noticed quickly, but the
        callback only fires when the worker actually reported something new
        (issue #244) — the consumer's job is a DB write, and re-reporting an
        identical status half a second later buys nothing.
        """
        last_reported = None
        async with httpx.AsyncClient() as client:
            while not stop_event.is_set():
                try:
                    resp = await client.get(
                        f"{self.remote_url}/v1/status", timeout=2.0, headers=self._headers
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("active"):
                            progress = data.get("progress", 0.0)
                            message = data.get("message")
                            if (progress, message) != last_reported:
                                last_reported = (progress, message)
                                # We pass None for duration to the callback,
                                # because the Jetson message already handles the time logic
                                progress_callback(progress, None, message)
                except httpx.RequestError as e:
                    logger.debug(f"Progress polling failed: {e}")

                # Check 2 times a second
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

    async def _wait_for_server_idle(
        self, blocking_file: str, progress_callback: Optional[Callable]
    ) -> None:
        """Poll /v1/status until the remote server is no longer active.

        Bounded on both axes (issue #195): a deadline for "busy forever" and a
        consecutive-error cap for "went away mid-wait". Either raises
        ProviderUnavailableError, which unblocks the serial queue.
        """
        logger.info(
            f"Remote server busy with '{blocking_file}'. "
            f"Waiting for it to finish before retrying..."
        )
        deadline = time.monotonic() + self.timeout
        consecutive_errors = 0
        async with httpx.AsyncClient() as client:
            while True:
                if time.monotonic() >= deadline:
                    raise ProviderUnavailableError(
                        f"Gave up waiting for the worker to finish '{blocking_file}' "
                        f"after {self.timeout}s — will retry."
                    )
                await asyncio.sleep(WAIT_FOR_IDLE_POLL_INTERVAL_SEC)
                try:
                    resp = await client.get(
                        f"{self.remote_url}/v1/status", timeout=10.0, headers=self._headers
                    )
                    consecutive_errors = 0
                    if resp.status_code == 200:
                        data = resp.json()
                        if progress_callback:
                            progress_callback(
                                data.get("progress", 0),
                                None,
                                f"Waiting: '{blocking_file}' in progress...",
                            )
                        if not data.get("active"):
                            logger.info("Remote server is now free. Retrying upload.")
                            return
                except httpx.RequestError as e:
                    consecutive_errors += 1
                    logger.warning(
                        f"Status poll error while waiting for '{blocking_file}' "
                        f"({consecutive_errors}/{WAIT_FOR_IDLE_MAX_POLL_ERRORS}): {e}"
                    )
                    if consecutive_errors >= WAIT_FOR_IDLE_MAX_POLL_ERRORS:
                        raise ProviderUnavailableError(
                            f"Remote worker became unreachable while waiting for "
                            f"'{blocking_file}' to finish ({consecutive_errors} "
                            f"consecutive poll failures) — will retry."
                        )

    def _checkpoint_params(self, audio_path: str) -> dict:
        """Identity of a job on the worker: original filename + byte size.

        Matches the worker's own checkpoint key derivation, but we send the two
        raw values rather than a hash so the formula lives in exactly one place
        (jetson/server.py) and can change without a lockstep deploy.
        """
        return {
            "filename": os.path.basename(audio_path),
            "size": os.path.getsize(audio_path),
        }

    async def _probe_checkpoint(self, audio_path: str) -> dict:
        """
        Ask the worker whether it holds resumable progress for this file.

        Purely an optimisation, so every failure mode — an older worker with no
        such route, a transport error, malformed JSON — degrades to "no
        checkpoint" and the normal upload path. The worker resumes from its own
        checkpoint after a re-upload anyway; skipping the probe only costs
        bandwidth.
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.remote_url}/v1/checkpoint",
                    params=self._checkpoint_params(audio_path),
                    timeout=10.0,
                    headers=self._headers,
                )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("exists"):
                    return data
        except (httpx.RequestError, ValueError) as e:
            logger.debug(f"Checkpoint probe failed (will upload normally): {e}")
        return {}

    @staticmethod
    def _paused_payload(data) -> Optional[dict]:
        """Return the payload if the worker reported a pause rather than a result."""
        if isinstance(data, dict) and data.get("status") == "paused":
            return data
        return None

    @staticmethod
    def _raise_paused(data: dict, filename: str) -> None:
        completed = data.get("completed_through_sec", 0)
        logger.info(
            f"Remote worker paused {filename} at {completed}s "
            f"({data.get('progress', 0)*100:.0f}%) — progress is checkpointed."
        )
        raise TranscriptionPaused(
            f"Transcription of {filename} paused at {completed}s of audio",
            completed_through_sec=completed,
            progress=data.get("progress", 0.0),
        )

    async def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable] = None,
    ) -> List[TranscribedSentence]:
        """Upload audio to the remote server and return transcribed sentences."""
        if not self.remote_url:
            raise ProviderUnavailableError("Remote URL is not configured.")

        filename = os.path.basename(audio_path)
        logger.info(f"Preparing to upload {filename} to {self.remote_url} ...")

        # Check if the Orin already has a cached result for this file (e.g. after a server
        # restart where the previous upload completed but the result was never received).
        # This avoids re-uploading and re-transcribing a multi-hour job unnecessarily.
        try:
            async with httpx.AsyncClient() as check_client:
                cached_check = await check_client.get(
                    f"{self.remote_url}/v1/result/{filename}", timeout=10.0, headers=self._headers
                )
            if cached_check.status_code == 200:
                logger.info(f"Found cached result for {filename} on remote server — skipping upload.")
                data = cached_check.json()
                sentences_data = data.get("sentences", [])
                return [
                    TranscribedSentence(
                        text=s.get("text", ""),
                        start_ms=s.get("start_ms", 0),
                        end_ms=s.get("end_ms", 0),
                    )
                    for s in sentences_data
                ]
        except httpx.RequestError as e:
            logger.debug(f"Pre-flight cached result check failed (will proceed with upload): {e}")

        # Second pre-flight: a job paused for the off-hours window (#106) leaves
        # a checkpoint on the worker, and the worker keeps the audio alongside
        # it. When both are there we resume with a small JSON call instead of
        # pushing multiple GB back over the wire.
        checkpoint = await self._probe_checkpoint(audio_path)
        use_resume = bool(checkpoint.get("audio_retained"))
        if checkpoint:
            logger.info(
                f"Remote worker holds a checkpoint for {filename} at "
                f"{checkpoint.get('completed_through_sec', 0)}s "
                f"({'resuming in place' if use_resume else 'audio gone — re-uploading'})."
            )

        # Start the polling background task if a callback is provided
        stop_polling = asyncio.Event()
        poll_task = None
        if progress_callback:
            poll_task = asyncio.create_task(self._poll_progress(stop_polling, progress_callback))

        try:
            data = None
            for _attempt in range(10):
                attempt_label = f" (attempt {_attempt + 1})" if _attempt > 0 else ""
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    try:
                        if use_resume:
                            logger.info(
                                f"Sending POST /v1/transcribe/resume request{attempt_label} "
                                f"(no upload — worker still holds the audio)..."
                            )
                            response = await client.post(
                                f"{self.remote_url}/v1/transcribe/resume",
                                json=self._checkpoint_params(audio_path),
                                headers=self._headers,
                            )
                        else:
                            logger.info(f"Sending POST /v1/transcribe request{attempt_label}...")
                            with open(audio_path, "rb") as f:
                                response = await client.post(
                                    f"{self.remote_url}/v1/transcribe",
                                    files={"audio_file": (filename, f, "audio/mpeg")},
                                    headers=self._headers,
                                )
                    except httpx.ConnectError as e:
                        raise ProviderUnavailableError(f"Connection to remote server failed: {e}")
                    except httpx.ReadTimeout as e:
                        raise ProviderUnavailableError(f"Remote transcription timed out after {self.timeout}s: {e}")
                    except httpx.RequestError as e:
                        raise ProviderUnavailableError(f"HTTP request error: {e}")

                # The retained audio vanished between the probe and the request
                # (e.g. the worker's /tmp was swept). Fall back to uploading —
                # the checkpoint itself still shortcuts the work.
                if use_resume and response.status_code == 404:
                    logger.info(
                        f"Worker no longer has the audio for {filename} — uploading it again."
                    )
                    use_resume = False
                    continue

                # Handle response codes
                if response.status_code == 409:
                    # The 409 body tells us exactly what file the Orin is currently working on.
                    # Use that directly rather than making a separate status call, which can
                    # race against state changes between the 409 and the follow-up GET.
                    try:
                        conflict_data = response.json()
                        current_file_on_orin = conflict_data.get("current_job", {}).get("file")
                    except Exception:
                        current_file_on_orin = None

                    if current_file_on_orin == filename:
                        # Orin is already transcribing OUR file (e.g. after a server restart).
                        # Re-attach by polling until it finishes, then fetch the cached result.
                        logger.info(
                            f"Re-attaching to ongoing transcription of {filename} on remote server."
                        )
                        # Track the Orin process instance so we can tell "job finished"
                        # apart from "the Orin process crashed and restarted" — both look
                        # like active=false, but only one of them has a cached result.
                        seen_instance_id = conflict_data.get("instance_id") if isinstance(conflict_data, dict) else None
                        restarted = False

                        # Bounded the same way as _wait_for_server_idle (#195):
                        # a deadline and a consecutive-error cap, because a
                        # worker that dies here is invisible to the exit
                        # conditions below — neither "active: false" nor an
                        # instance-id change is observable while it is silent.
                        reattach_deadline = time.monotonic() + self.timeout
                        poll_errors = 0

                        # Use a fresh client for polling so the upload client's state doesn't matter.
                        async with httpx.AsyncClient() as poll_client:
                            while True:
                                if time.monotonic() >= reattach_deadline:
                                    raise ProviderUnavailableError(
                                        f"Gave up trying to re-attach to {filename} on the "
                                        f"remote worker after {self.timeout}s — will retry."
                                    )
                                await asyncio.sleep(REATTACH_POLL_INTERVAL_SEC)
                                try:
                                    status_resp = await poll_client.get(
                                        f"{self.remote_url}/v1/status", timeout=10.0, headers=self._headers
                                    )
                                    poll_errors = 0
                                    if status_resp.status_code == 200:
                                        status_data = status_resp.json()
                                        current_instance_id = status_data.get("instance_id")
                                        if (
                                            seen_instance_id
                                            and current_instance_id
                                            and current_instance_id != seen_instance_id
                                        ):
                                            restarted = True
                                            break
                                        if current_instance_id:
                                            seen_instance_id = current_instance_id

                                        if progress_callback:
                                            progress_callback(
                                                status_data.get("progress", 0),
                                                None,
                                                status_data.get("message"),
                                            )
                                        if not status_data.get("active"):
                                            break
                                except httpx.RequestError as e:
                                    poll_errors += 1
                                    logger.warning(
                                        f"Status poll error while re-attached to {filename} "
                                        f"({poll_errors}/{REATTACH_MAX_POLL_ERRORS}): {e}"
                                    )
                                    if poll_errors >= REATTACH_MAX_POLL_ERRORS:
                                        raise ProviderUnavailableError(
                                            f"Remote worker became unreachable while we were "
                                            f"re-attached to {filename} ({poll_errors} "
                                            f"consecutive poll failures) — will retry."
                                        )

                        if restarted:
                            # The Orin process crashed and restarted mid-job — its in-memory
                            # result cache is gone. Retrying resumes from the on-disk checkpoint
                            # on the Orin rather than failing on a guaranteed-404 result fetch.
                            raise ProviderUnavailableError(
                                f"Remote server restarted while transcribing {filename} "
                                f"(likely crashed) — will retry."
                            )

                        # Transcription finished — fetch the cached result.
                        logger.info(f"Transcription of {filename} complete on remote. Fetching result...")
                        async with httpx.AsyncClient() as result_client:
                            result_resp = await result_client.get(
                                f"{self.remote_url}/v1/result/{filename}", timeout=60.0, headers=self._headers
                            )
                        if result_resp.status_code == 200:
                            data = result_resp.json()
                        else:
                            # No result, and the job is no longer active. If the
                            # worker holds a checkpoint, it paused for the
                            # off-hours window rather than failing — re-pend the
                            # item instead of erroring the book out.
                            paused_state = await self._probe_checkpoint(audio_path)
                            if paused_state:
                                self._raise_paused(
                                    {"progress": paused_state.get("progress", 0.0),
                                     "completed_through_sec": paused_state.get(
                                         "completed_through_sec", 0)},
                                    filename,
                                )
                            raise TranscriptionError(
                                f"Failed to fetch cached transcription result: "
                                f"{result_resp.status_code} - {result_resp.text}"
                            )
                        break

                    else:
                        # A different file is running. Wait for it to finish, then retry the upload.
                        await self._wait_for_server_idle(
                            current_file_on_orin or "unknown", progress_callback
                        )
                        continue

                elif response.status_code >= 500:
                    error_body = response.text
                    body_low = error_body.lower()
                    if any(kw in body_low for kw in ("out of memory", "oom", "cuda error", "cudaoutofmemory")):
                        # Treat as retriable — the server frees the job lock after OOM and
                        # accepts new work immediately. ProviderUnavailableError lets the queue retry.
                        raise ProviderUnavailableError(
                            f"Remote server ran out of memory (will retry): {error_body[:300]}"
                        )
                    if any(kw in body_low for kw in (
                        "failed to load audio chunk", "invalid data", "error submitting packet",
                        "error reading header", "moov atom not found",
                    )):
                        # The audio itself is undecodable (corrupt/truncated source). Retrying
                        # re-uploads the same bad file and fails again — fail fast instead so the
                        # queue surfaces a clear "re-import required" error.
                        raise TranscriptionError(
                            f"Remote server could not decode the audio — corrupt or incomplete "
                            f"source file, re-import required: {error_body[:300]}"
                        )
                    raise ProviderUnavailableError(
                        f"Remote server internal error ({response.status_code}): {error_body}"
                    )
                elif response.status_code != 200:
                    raise TranscriptionError(
                        f"Transcription failed ({response.status_code}): {response.text}"
                    )
                else:
                    # Success from the original POST
                    data = response.json()
                    break

            else:
                raise ProviderUnavailableError(
                    "Remote server remained busy after 10 upload attempts."
                )

            # A pause is a 200 carrying no sentences — the worker stopped at a
            # chunk boundary with its progress checkpointed (#106).
            paused = self._paused_payload(data)
            if paused:
                self._raise_paused(paused, filename)

            # Parse successful response
            sentences_data = data.get("sentences", [])

            sentences = []
            for s in sentences_data:
                sentences.append(TranscribedSentence(
                    text=s.get("text", ""),
                    start_ms=s.get("start_ms", 0),
                    end_ms=s.get("end_ms", 0),
                ))

            logger.info(
                f"Remote transcription complete: {len(sentences)} sentences in "
                f"{data.get('processing_time_seconds', '?')}s processing time "
                f"for {data.get('duration_seconds', '?')}s of audio."
            )
            return sentences

        finally:
            if poll_task:
                stop_polling.set()
                await poll_task

    async def is_available(self) -> bool:
        """
        Check if the remote server is reachable and healthy.

        Deliberately does **not** require ``model_loaded``: since #106 the
        worker loads its model on the first job and unloads it after an idle
        period, so an unloaded model is the normal resting state, not a fault.
        Gating on it would send every job to the local fallback — which isn't
        installed in the production image.
        """
        if not self.remote_url:
            return False

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.remote_url}/v1/health", headers=self._headers)
                if resp.status_code == 200:
                    return resp.json().get("status") == "healthy"
                return False
        except Exception as e:
            logger.debug(f"Remote provider health check failed: {e}")
            return False

    async def request_pause(self) -> bool:
        """Ask the worker to stop at its next chunk boundary and checkpoint."""
        if not self.remote_url:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.remote_url}/v1/pause", timeout=10.0, headers=self._headers
                )
            return resp.status_code == 200
        except httpx.RequestError as e:
            logger.warning(f"Could not request pause on remote worker: {e}")
            return False

    async def release_resources(self) -> None:
        """Ask the worker to drop its model weights now (frees ~4GB on the Orin)."""
        if not self.remote_url:
            return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.remote_url}/v1/unload", timeout=30.0, headers=self._headers
                )
        except httpx.RequestError as e:
            # Best effort: the worker's own idle timer is the backstop.
            logger.debug(f"Could not ask remote worker to unload: {e}")

    async def discard_checkpoint(self, audio_path: str) -> None:
        """Drop the worker's saved progress and retained audio for this file."""
        if not self.remote_url:
            return
        try:
            params = self._checkpoint_params(audio_path)
        except OSError as e:
            # The local audio is gone, so we can't derive the key. The worker's
            # 48h checkpoint sweep will collect it.
            logger.debug(f"Could not derive checkpoint key for {audio_path}: {e}")
            return
        try:
            async with httpx.AsyncClient() as client:
                await client.delete(
                    f"{self.remote_url}/v1/checkpoint",
                    params=params,
                    timeout=10.0,
                    headers=self._headers,
                )
        except httpx.RequestError as e:
            logger.debug(f"Could not discard remote checkpoint: {e}")

    def name(self) -> str:
        return f"Remote Whisper ({self.remote_url})"
