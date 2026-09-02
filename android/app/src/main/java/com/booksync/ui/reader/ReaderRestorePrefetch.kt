package com.booksync.ui.reader

import android.util.Log
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.PositionFetch
import com.booksync.data.repository.PositionSyncTimeouts
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "ReaderRestorePrefetch"

/**
 * The reader's pre-restore server pull, BOUNDED (issue #167) — the same shape
 * the player and AudioPlayerService use (contract § "The write gate": "pull
 * the server position (bounded, then fall back to cache) before seeking").
 *
 * Three best-effort calls, each capped at [timeoutMs]: refresh the bookmark
 * row, refresh the sync-map cache (issue #55), and fetch the canonical
 * record. A network that accepts connections but never answers (captive
 * portal, SYN black-hole) used to stall a *downloaded* book's open for
 * minutes on these awaits; now the worst case is 3 × [timeoutMs] before the
 * reader falls back to the local cache, exactly like the player.
 *
 * Returns the canonical fetch; a timed-out fetch reports
 * `reachable = false`, which is what lets the caller fall back to the local
 * bookmark row (the same rule an unreachable server triggers).
 */
suspend fun prefetchBeforeRestore(
    repository: BookSyncRepository,
    pairId: Int,
    timeoutMs: Long = PositionSyncTimeouts.SERVER_POSITION_TIMEOUT_MS,
): PositionFetch {
    withTimeoutOrNull(timeoutMs) {
        repository.refreshBookmark(pairId)
    } ?: Log.w(TAG, "server position not available in time — using local cache")

    withTimeoutOrNull(timeoutMs) {
        repository.ensureSyncMapCached(pairId)
    } ?: Log.w(TAG, "sync map not available in time — using local cache")

    return withTimeoutOrNull(timeoutMs) {
        repository.fetchPosition("pair", pairId)
    } ?: PositionFetch(null, reachable = false)
}

/**
 * The same bounded pre-restore pull, for a standalone (unpaired) ebook — issue
 * #169.
 *
 * One call rather than three. `refreshBookmark` and `ensureSyncMapCached` are
 * both pair-keyed and neither has a standalone meaning: `bookmarks` has no local
 * row for a standalone ebook, and with no audiobook there is nothing to align a
 * sync map to. The rule that matters is unchanged — contract § "The write gate",
 * "Resume paths refresh first": pull the server position before seeking, bounded,
 * and fall back to the local cache on timeout.
 */
suspend fun prefetchBeforeRestoreStandalone(
    repository: BookSyncRepository,
    ebookId: Int,
    timeoutMs: Long = PositionSyncTimeouts.SERVER_POSITION_TIMEOUT_MS,
): PositionFetch =
    withTimeoutOrNull(timeoutMs) {
        repository.fetchPosition("ebook", ebookId)
    } ?: PositionFetch(null, reachable = false).also {
        Log.w(TAG, "server position not available in time — using local cache")
    }
