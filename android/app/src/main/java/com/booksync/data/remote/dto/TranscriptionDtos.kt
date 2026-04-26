package com.booksync.data.remote.dto

import kotlinx.serialization.Serializable

/**
 * Data Transfer Objects for the transcription API.
 * Mirror the server's Pydantic schemas in server/schemas.py.
 */

/**
 * Response from `POST /api/transcription/{pairId}/start`
 * and `GET /api/transcription/queue`.
 *
 * [status] matches the server's queue item status:
 *   "pending" / "processing" / "completed" / "failed" / "cancelled"
 * [progress] is 0.0–1.0 (multiply by 100 for percent).
 */
@Serializable
data class QueueItemResponse(
    val id: Int,
    val book_pair_id: Int,
    val book_title: String? = null,
    val status: String,
    val priority: Int,
    val position: Int = 0,
    val progress: Float? = null,
    val message: String? = null,
    val error_message: String? = null,
    val retry_count: Int = 0,
    val created_at: String,
    val started_at: String? = null,
    val completed_at: String? = null,
)

/**
 * Response from `GET /api/transcription/{pairId}/status`.
 *
 * [status] is the book pair's PairStatus:
 *   "unpaired" / "matched" / "pending" / "processing" / "synced" / "failed"
 * [progress] is 0.0–1.0 when status == "processing".
 */
@Serializable
data class TranscriptionStatusResponse(
    val book_pair_id: Int,
    val status: String,
    val progress: Float? = null,
    val message: String? = null,
)

/**
 * Sealed class representing the UI-facing transcription state for a given pair.
 * Derived from [TranscriptionStatusResponse] or [QueueItemResponse].
 */
sealed class TranscriptionStatus {
    /** No sync map and not in queue. */
    object NotTranscribed : TranscriptionStatus()
    /** In queue, waiting for a worker to pick it up. */
    data class Queued(val position: Int) : TranscriptionStatus()
    /** Currently being processed. [progressPercent] is 0–100. */
    data class Transcribing(val progressPercent: Int) : TranscriptionStatus()
    /** Sync map exists and is ready. */
    object Transcribed : TranscriptionStatus()
    /** Transcription failed. [message] is the error. */
    data class Failed(val message: String?) : TranscriptionStatus()

    /** Map from the server's pair `status` string to a UI state (no queue item present). */
    companion object {
        fun fromPairStatus(status: String, progress: Float?): TranscriptionStatus = when (status) {
            "synced"     -> Transcribed
            "failed"     -> Failed(null)
            "processing" -> Transcribing(((progress ?: 0f) * 100).toInt())
            "pending"    -> Queued(0)
            else         -> NotTranscribed  // matched, unpaired
        }

        fun fromQueueItem(item: QueueItemResponse): TranscriptionStatus = when (item.status) {
            "processing" -> Transcribing(((item.progress ?: 0f) * 100).toInt())
            "completed"  -> Transcribed
            "failed", "cancelled" -> Failed(item.error_message ?: item.message)
            else         -> Queued(item.position)  // pending
        }
    }
}
