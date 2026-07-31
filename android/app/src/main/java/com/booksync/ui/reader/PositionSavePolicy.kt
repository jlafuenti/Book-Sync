package com.booksync.ui.reader

/**
 * Decides whether a reader-position save may write real anchors (chapter,
 * sentence, locator, audio, percent) to the server and Room, or must fall
 * back to stamping local metadata only.
 *
 * Replaces [ReaderActivity]'s old `positionEstablished` boolean latch
 * (issue #61/#40), which had two defects:
 *  (a) an unresolved restore (every rung in the ladder failed — e.g. an
 *      audio-only ladder when there's no sync map yet, so
 *      `repository.audioToEpubText` returns `Pair(0, "")`) left the flag
 *      false FOREVER, so every later save — including ones after the user
 *      had turned real pages — was suppressed for the whole session;
 *  (b) the catch block in `getInitialLocator` reset the flag to false even
 *      after an earlier rung had already landed and set it true, undoing a
 *      legitimate success.
 *
 * [PositionSavePolicy] fixes both: [RestoreOutcome] only ever moves
 * `Unresolved -> Landed` (never back — see [onRestoreOutcome]), and there is
 * no "suppress everything" verdict. An unresolved restore still produces
 * [SaveVerdict.LocalMetadataOnly] rather than nothing at all, so
 * `BookSyncRepository.resolvePairOpenTarget` (which keys off the local
 * bookmark's `source`) keeps routing to the reader. Once the user turns a
 * page the verdict upgrades to [SaveVerdict.FullSave] even though the
 * restore itself never resolved — the current view is now something the
 * user chose, not a failed guess.
 */
class PositionSavePolicy {

    /** How the restore ladder concluded for this session — set once per open, see [onRestoreOutcome]. */
    enum class RestoreOutcome {
        /** No steps at all — the book is genuinely unread; opening at the start is correct. */
        Unread,
        /** Every rung failed to resolve; the current view is a failed guess, not a chosen position. */
        Unresolved,
        /** A rung resolved; the reader is showing a position it can trust. */
        Landed,
    }

    /** What a save call should do with the anchors it captured. */
    enum class SaveVerdict {
        /** Write real anchors: server PUT + Room row. */
        FullSave,
        /** Stamp only the local bookmark's `source`/`updatedAt`; anchors untouched, nothing sent. */
        LocalMetadataOnly,
    }

    private var outcome: RestoreOutcome = RestoreOutcome.Unresolved
    private var userNavigated: Boolean = false

    /**
     * Records the restore ladder's outcome. Monotonic: once [RestoreOutcome.Landed]
     * has been recorded, a later call can never move it back — this is exactly
     * what the old boolean-flag bug did (an exception thrown AFTER a rung had
     * already landed reset the flag to false and re-suppressed saves for a
     * session that actually had a good restore).
     */
    fun onRestoreOutcome(newOutcome: RestoreOutcome) {
        if (outcome == RestoreOutcome.Landed && newOutcome != RestoreOutcome.Landed) return
        outcome = newOutcome
    }

    /** Records that the user has navigated (e.g. turned a page) since the restore ran. */
    fun onUserNavigation() {
        userNavigated = true
    }

    /** What the next save call should do. */
    fun verdictForSave(): SaveVerdict =
        if (outcome != RestoreOutcome.Unresolved || userNavigated) {
            SaveVerdict.FullSave
        } else {
            SaveVerdict.LocalMetadataOnly
        }
}
