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

    /**
     * Whether one `DownloadWorker` run for a [BookPairEntity] should also fetch
     * the sync map (issue #655).
     *
     * `SYNC_MAP` (the explicit "Refresh sync data" action and the [pairsToFetch]
     * sweep) and `AUDIOBOOK` always did; `ALL` always did unless the cache was
     * already current. An `EBOOK`-only download never did — the sync map is a
     * pair-level artifact, not part of either file, so nothing forced it to ride
     * along, and a device that only ever downloads ebooks could stay cold
     * forever. [downloadWithEbookEnabled] is the "Download sync maps with the
     * ebook" setting (`AccountViewModel`, default on) that closes that gap;
     * every other type ignores it, so the setting cannot leak into a download it
     * was not named for.
     */
    fun shouldFetchSyncMapFor(type: String, alreadyCached: Boolean, downloadWithEbookEnabled: Boolean): Boolean =
        when (type) {
            "SYNC_MAP", "AUDIOBOOK" -> true
            "ALL" -> !alreadyCached
            "EBOOK" -> downloadWithEbookEnabled
            else -> false
        }

    /**
     * Whether a sync-map fetch that [shouldFetchSyncMapFor] would otherwise run
     * should be held back because of the "Only download sync maps over Wi-Fi"
     * setting (issue #655).
     *
     * `DownloadWorker` is the only caller: every path that pulls a sync map in
     * the background or as a side effect of another download goes through it.
     * `AudioPlayerService.refreshPositionBeforeResume`'s bounded fallback fetch
     * (`MediaDownloadRepository.ensureSyncMapCached`) does not call this and
     * must not start calling it — on a cellular connection with the setting on,
     * it is the only thing standing between the user and the stale-position bug
     * issue #643 fixed. That trade is real and is spelled out in the setting's
     * own description, not hidden here.
     */
    fun blockedByMeteredConnection(wifiOnlyEnabled: Boolean, isMetered: Boolean): Boolean =
        wifiOnlyEnabled && isMetered
}
