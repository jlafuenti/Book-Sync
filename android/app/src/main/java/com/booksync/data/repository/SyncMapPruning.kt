package com.booksync.data.repository

import com.booksync.data.local.entity.BookPairEntity

/**
 * The pure decision behind pruning cached sync maps for streamed books
 * (issue #678).
 *
 * The policy: a pair keeps its cached sync map only while it has a
 * downloaded ebook or audiobook, or while it is open right now (the reader
 * or the player/Android Auto — tracked by [SyncMapInUse], not this file, so
 * this stays a pure function [LibraryViewModel] can call on every refresh).
 * Deleting the last download already clears the map immediately
 * ([MediaDownloadRepository.deleteEbook]/`deleteAudiobook`); this is the
 * other half — a pair that was only ever streamed, cached its map because it
 * was opened, and is not open any more.
 *
 * Never touches a downloaded pair's map, even if [inUsePairIds] disagrees —
 * `syncMapDownloaded && (ebookDownloaded || audiobookDownloaded)` is not in
 * the candidate set at all, so a stale or wrong in-use registration can only
 * ever fail to protect a streamed pair's map one refresh longer, never delete
 * a downloaded one's.
 */
object SyncMapPruning {

    /**
     * Ids of the pairs in [pairs] whose cached sync map should be cleared:
     * a map is cached, nothing is downloaded, and the pair is not in
     * [inUsePairIds].
     */
    fun pairsToPrune(pairs: List<BookPairEntity>, inUsePairIds: Set<Int>): List<Int> =
        pairs.filter { pair ->
            pair.syncMapDownloaded &&
                !pair.ebookDownloaded &&
                !pair.audiobookDownloaded &&
                pair.id !in inUsePairIds
        }.map { it.id }
}
