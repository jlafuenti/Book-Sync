package com.booksync.data.repository

import com.booksync.data.local.entity.BookPairEntity

/**
 * The sync map arrives on its own once a downloaded pair is transcribed
 * (issue #537). Before this, `DownloadWorker` only fetched the sync map as
 * part of a download (`ALL` / `AUDIOBOOK` / explicit `SYNC_MAP`); a 404 at
 * that moment is correctly treated as "not ready yet", but nothing came back
 * for it later — switching formats stayed broken until the user found
 * "Refresh sync data" by hand.
 *
 * Pure decisions only, so [LibraryViewModel] can call them in one-liners and
 * they're testable without Hilt/WorkManager.
 */
object SyncMapAutoFetch {

    /** A synced pair with a local ebook or audiobook and no cached sync map should fetch it (issue #537). */
    fun needsSyncMapFetch(pair: BookPairEntity): Boolean =
        pair.status == "synced" && (pair.ebookDownloaded || pair.audiobookDownloaded) && !pair.syncMapDownloaded

    /** IDs of the pairs in [pairs] that [needsSyncMapFetch], in list order. */
    fun pairsToFetch(pairs: List<BookPairEntity>): List<Int> =
        pairs.filter(::needsSyncMapFetch).map { it.id }

    /**
     * IDs that were in [previous] but are missing from [current] — pairs that
     * just left the active transcription queue.
     *
     * [previous] is null for the first emission after (re)subscribing (e.g.
     * app start): that emission is a baseline, not a transition, and treating
     * it as one would fire a spurious refresh every time the app opens.
     */
    fun leftActiveSet(previous: Set<Int>?, current: Set<Int>): Set<Int> =
        if (previous == null) emptySet() else previous - current
}
