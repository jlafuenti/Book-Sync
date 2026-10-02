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
 * Pure decisions only, so [LibraryLoader] (the sweep) and the screens can call them in
 * one-liners and they're testable without Hilt/WorkManager.
 */
object SyncMapAutoFetch {

    /** A synced pair with a local ebook or audiobook and no cached sync map should fetch it (issue #537). */
    fun needsSyncMapFetch(pair: BookPairEntity): Boolean =
        isSyncMapMissing(
            synced = pair.status == "synced",
            anythingDownloaded = pair.ebookDownloaded || pair.audiobookDownloaded,
            mapCached = pair.syncMapDownloaded,
        )

    /**
     * The one rule behind both the automatic sweep and the manual "Download sync data" row:
     * the server has the map (the pair is synced), the device holds a book that needs it, and
     * no map is cached. [needsSyncMapFetch] applies it to a pair; the card overflow sheet applies
     * it to the fields its `OverflowTarget.Pair` carries, so the two cannot drift apart.
     */
    fun isSyncMapMissing(synced: Boolean, anythingDownloaded: Boolean, mapCached: Boolean): Boolean =
        synced && anythingDownloaded && !mapCached

    /**
     * Whether Book Details (and the card menu) should show "Download sync data" (issue #786).
     *
     * Same condition as [needsSyncMapFetch], and deliberately **without** a hand-removed
     * exclusion: the sweep stays away from a map the user removed ([pairsToFetch]'s `removedIds`,
     * issue #678), but the user must be able to undo that, and this row is the way back. It is
     * also the only action available for a map the server replaced while the sweep could not run.
     */
    fun offersSyncMapDownload(pair: BookPairEntity): Boolean = needsSyncMapFetch(pair)

    /**
     * IDs of the pairs in [pairs] that [needsSyncMapFetch], excluding any id in
     * [removedIds], in list order.
     *
     * [removedIds] is [SyncMapRemovalStore]'s "removed by you" set (issue
     * #678): without this exclusion, a map the user explicitly removed —
     * "Remove sync data" or Account → Storage's "Clear" — would look
     * identical to one that simply was never fetched, and this sweep would
     * silently re-download it on the very next library refresh. A fresh
     * download or "Refresh sync data" clears the id from that set
     * ([com.booksync.data.repository.MediaDownloadRepository.downloadSyncMap]),
     * which is what lets the pair become eligible again.
     */
    fun pairsToFetch(pairs: List<BookPairEntity>, removedIds: Set<Int> = emptySet()): List<Int> =
        pairs.filter { needsSyncMapFetch(it) && it.id !in removedIds }.map { it.id }

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
     * `SYNC_MAP` (the background sweep, [pairsToFetch]), `SYNC_MAP_EXPLICIT`
     * (the "Refresh sync data" button) and `AUDIOBOOK` always did; `ALL` always did unless the
     * cache was already current. An `EBOOK`-only download never did — the sync
     * map is a pair-level artifact, not part of either file, so nothing forced
     * it to ride along, and a device that only ever downloads ebooks could stay
     * cold forever. [downloadWithEbookEnabled] is the "Download sync maps with
     * the ebook" setting (`AccountViewModel`, default on) that closes that gap;
     * every other type ignores it, so the setting cannot leak into a download it
     * was not named for.
     */
    fun shouldFetchSyncMapFor(type: String, alreadyCached: Boolean, downloadWithEbookEnabled: Boolean): Boolean =
        when (type) {
            "SYNC_MAP", "SYNC_MAP_EXPLICIT", "AUDIOBOOK" -> true
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
     *
     * [type] `"SYNC_MAP_EXPLICIT"` (the "Refresh sync data" button) always
     * answers `false`, regardless of the other two arguments: a user who tapped
     * a button asking for the data now gets it now, on whatever connection is
     * available. The setting's label — "Only download sync maps **over
     * Wi-Fi**" — is a statement about background work, and silently doing
     * nothing in response to an explicit tap looks exactly like a broken
     * button. Every background path (the periodic sweeps above, and a fetch
     * bundled with another download) passes its own type and stays gated.
     */
    fun blockedByMeteredConnection(type: String, wifiOnlyEnabled: Boolean, isMetered: Boolean): Boolean =
        type != "SYNC_MAP_EXPLICIT" && wifiOnlyEnabled && isMetered
}
