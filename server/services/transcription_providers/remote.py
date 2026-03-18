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
from typing import List, Optional, Callable

from services.transcription_providers.base import (
    TranscriptionProvider,
    ProviderUnavailableError,
    TranscriptionError,
)
from services.transcription import TranscribedSentence

import httpx

logger = logging.getLogger(__name__)


class RemoteWhisperProvider(TranscriptionProvider):
    """
    Sends audio to a remote faster-whisper HTTP API for transcription.
    Intended for use with the Jetson Orin Nano transcription server.
    """

    def __init__(self, remote_url: str, timeout: int = 7200):
        self.remote_url = remote_url.rstrip("/")
        self.timeout = timeout
        
    async def _poll_progress(self, stop_event: asyncio.Event, progress_callback: Callable):
        """Polls the remote server for progress while transcription is running."""
        async with httpx.AsyncClient() as client:
            while not stop_event.is_set():
                try:
                    resp = await client.get(f"{self.remote_url}/v1/status", timeout=2.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("active"):
                            progress = data.get("progress", 0.0)
                            message = data.get("message")
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
                    f"{self.remote_url}/v1/result/{filename}", timeout=10.0
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

        # Start the polling background task if a callback is provided
        stop_polling = asyncio.Event()
        poll_task = None
        if progress_callback:
            poll_task = asyncio.create_task(self._poll_progress(stop_polling, progress_callback))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                with open(audio_path, "rb") as f:
                    logger.info("Sending POST /v1/transcribe request...")
                    files = {"audio_file": (filename, f, "audio/mpeg")}
                    
                    try:
                        response = await client.post(
                            f"{self.remote_url}/v1/transcribe", 
                            files=files
                        )
                    except httpx.ConnectError as e:
                        raise ProviderUnavailableError(f"Connection to remote server failed: {e}")
                    except httpx.ReadTimeout as e:
                        raise ProviderUnavailableError(f"Remote transcription timed out after {self.timeout}s: {e}")
                    except httpx.RequestError as e:
                        raise ProviderUnavailableError(f"HTTP request error: {e}")

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
                        # Use a fresh client for polling so the upload client's state doesn't matter.
                        async with httpx.AsyncClient() as poll_client:
                            while True:
                                await asyncio.sleep(10)
                                try:
                                    status_resp = await poll_client.get(
                                        f"{self.remote_url}/v1/status", timeout=10.0
                                    )
                                    if status_resp.status_code == 200:
                                        status_data = status_resp.json()
                                        if progress_callback:
                                            progress_callback(
                                                status_data.get("progress", 0),
                                                None,
                                                status_data.get("message"),
                                            )
                                        if not status_data.get("active"):
                                            break
                                except httpx.RequestError as e:
                                    logger.debug(f"Status poll error (retrying): {e}")

                        # Transcription finished — fetch the cached result.
                        logger.info(f"Transcription of {filename} complete on remote. Fetching result...")
                        async with httpx.AsyncClient() as result_client:
                            result_resp = await result_client.get(
                                f"{self.remote_url}/v1/result/{filename}", timeout=60.0
                            )
                        if result_resp.status_code == 200:
                            data = result_resp.json()
                        else:
                            raise TranscriptionError(
                                f"Failed to fetch cached transcription result: "
                                f"{result_resp.status_code} - {result_resp.text}"
                            )
                    else:
                        raise ProviderUnavailableError("Remote server is busy with another transcription.")
                
                elif response.status_code >= 500:
                    error_body = response.text
                    if any(kw in error_body.lower() for kw in ("out of memory", "oom", "cuda error", "cudaoutofmemory")):
                        raise TranscriptionError(
                            f"Remote server ran out of memory transcribing this file. "
                            f"Consider using a smaller Whisper model (e.g. 'small') or check if the audiobook is unusually large. "
                            f"Server error: {error_body[:300]}"
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
        """Check if the remote server is reachable by hitting /v1/health."""
        if not self.remote_url:
            return False

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.remote_url}/v1/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("model_loaded", False)
                return False
        except Exception as e:
            logger.debug(f"Remote provider health check failed: {e}")
            return False

    def name(self) -> str:
        return f"Remote Whisper ({self.remote_url})"
