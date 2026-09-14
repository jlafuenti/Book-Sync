package com.booksync.data.repository

import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.remote.dto.TranscriptionStatus

/**
 * The pure decision behind [TranscriptionRepository.readiness] (issue #535):
 * what to tell the user before opening/switching a pair. Null = ready, no
 * dialog needed.
 */
object PairReadiness {

    /** What to tell the user before opening/switching a pair. Null = ready, no dialog needed. */
    fun readinessFor(pair: BookPairEntity, queueItems: List<QueueItemResponse>): TranscriptionStatus? {
        if (pair.status == "synced" || pair.syncMapDownloaded) return null

        val queueItem = queueItems.firstOrNull { it.book_pair_id == pair.id }
        return if (queueItem != null) {
            TranscriptionStatus.fromQueueItem(queueItem)
        } else {
            TranscriptionStatus.fromPairStatus(pair.status, null)
        }
    }
}
