package com.booksync.data.repository

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.util.NetworkMonitor
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Repository for transcription queue operations.
 *
 * Offline policy:
 *   - [addToQueue] and [cancel] require a live server connection — they return
 *     [Result.failure] with [OfflineException] when the device is offline.
 *   - [observeStatus] and [activeQueueItemsFlow] are polling flows; they emit
 *     whatever the server last returned and stop updating while offline.
 */
@Singleton
class TranscriptionRepository @Inject constructor(
    private val api: BookSyncApi,
    private val networkMonitor: NetworkMonitor,
) {

    /** Thrown when a network-only mutation is attempted while offline. */
    class OfflineException : Exception("Device is offline")

    // -------------------------------------------------------------------------
    // Mutations (require connectivity)
    // -------------------------------------------------------------------------

    /**
     * Add [pairId] to the transcription queue.
     * Returns [Result.failure(OfflineException)] when the device is offline.
     */
    suspend fun addToQueue(pairId: Int): Result<QueueItemResponse> {
        if (!networkMonitor.isOnline.value) return Result.failure(OfflineException())
        return runCatching { api.startTranscription(pairId) }
    }

    /**
     * Cancel transcription for [pairId].
     * Returns [Result.failure(OfflineException)] when the device is offline.
     */
    suspend fun cancel(pairId: Int): Result<QueueItemResponse> {
        if (!networkMonitor.isOnline.value) return Result.failure(OfflineException())
        return runCatching { api.cancelTranscription(pairId) }
    }

    // -------------------------------------------------------------------------
    // Polling flows
    // -------------------------------------------------------------------------

    /**
     * Poll `GET /api/transcription/{pairId}/status` every [intervalMs] milliseconds
     * and emit the derived [TranscriptionStatus].
     *
     * Polling stops automatically when:
     *   - the returned status is [TranscriptionStatus.Transcribed] or [TranscriptionStatus.Failed]
     *   - the collecting coroutine is cancelled
     *
     * A longer [intervalMs] is used when the device is offline to avoid hammering the
     * network stack with failed requests.
     */
    fun observeStatus(
        pairId: Int,
        intervalMs: Long = POLL_STATUS_INTERVAL_MS,
    ): Flow<TranscriptionStatus> = flow {
        while (true) {
            if (networkMonitor.isOnline.value) {
                val status = runCatching { api.getTranscriptionStatus(pairId) }
                    .getOrNull()
                    ?.let { TranscriptionStatus.fromPairStatus(it.status, it.progress) }
                    ?: TranscriptionStatus.NotTranscribed

                emit(status)

                // Terminal states — stop polling.
                if (status is TranscriptionStatus.Transcribed || status is TranscriptionStatus.Failed) break
            }
            delay(intervalMs)
        }
    }

    /**
     * Poll `GET /api/transcription/queue` every [intervalMs] milliseconds.
     * Returns an empty list while offline instead of throwing.
     *
     * Intended for the Home screen "In Queue" section.
     */
    fun activeQueueItemsFlow(
        intervalMs: Long = POLL_QUEUE_INTERVAL_MS,
    ): Flow<List<QueueItemResponse>> = flow {
        while (true) {
            val items = if (networkMonitor.isOnline.value) {
                runCatching { api.getTranscriptionQueue() }.getOrElse { emptyList() }
                    .filter { it.status == "pending" || it.status == "processing" }
            } else {
                emptyList()
            }
            emit(items)
            delay(intervalMs)
        }
    }

    companion object {
        private const val POLL_STATUS_INTERVAL_MS = 5_000L
        private const val POLL_QUEUE_INTERVAL_MS  = 10_000L
    }
}
