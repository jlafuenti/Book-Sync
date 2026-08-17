package com.booksync.ui.reader

import com.booksync.SyncState
import com.booksync.data.repository.BookSyncRepository

/**
 * What a successful page→audio match writes before the reader hands off to the
 * player (issue #114).
 *
 * Lifted out of [ReaderActivity] so it can be tested without a WebView: the
 * activity does the DOM extraction and the sync-point lookup, this does the
 * bookkeeping. It deliberately mirrors the text-selection path
 * (`syncSelectedTextToAudio`) — the two differ only in where the text came
 * from, and when they drifted apart the page path quietly wrote less:
 *
 *  * [SyncState.pendingAudioSeekMs] is how the player seeks to the matched
 *    second immediately, instead of resolving a position from the server and
 *    racing the position poller;
 *  * `locatorAudioMs` pairs the page the user is on with that audio position, so
 *    coming back to the reader within ~30s lands on this page rather than one
 *    re-derived from the anchor.
 *
 * The write goes through `updateBookmark` — the canonical path described in
 * `docs/position-sync-contract.md`. Claiming `source = "ebook"` is correct here:
 * the user is acting from the reader on the page they can see.
 */
object PageAudioHandoff {

    /**
     * Records a matched handoff. Returns false — writing nothing at all — when
     * [audioMs] carries no match, which is what `epubToAudioText` returns as 0.
     */
    suspend fun apply(
        repository: BookSyncRepository,
        pairId: Int,
        chapterIndex: Int,
        locatorJson: String?,
        audioMs: Int,
    ): Boolean {
        if (audioMs <= 0) return false

        SyncState.pendingAudioSeekMs = audioMs.toLong()
        repository.updateBookmark(
            pairId = pairId,
            source = "ebook",
            epubChapter = chapterIndex,
            audioPositionMs = audioMs,
            epubLocator = locatorJson,
            locatorAudioMs = audioMs,
        )
        return true
    }
}
