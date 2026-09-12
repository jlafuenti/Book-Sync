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

    /**
     * Whether the displayed position is something the user chose rather than
     * where the restore ladder put them (issue #477).
     *
     * A save still happens either way — this does not gate [verdictForSave].
     * What it gates is the *audio* half: `ReaderActivity` passes it into
     * [ReaderPositionSnapshot.skipSyncPointLookup], because resolving a
     * sync-point match from a page nobody navigated to can only overwrite a
     * real listening position with a guess. That is how a four-and-a-half-hour
     * position became 340 ms: the ladder landed confidently on a title page and
     * the save mapped it back through the sync map.
     */
    fun hasUserNavigated(): Boolean = userNavigated

    /**
     * What the next save call should do.
     *
     * [atStartOfBook] is the hard safety net (issue #61/#40 fix 1b): Readium's
     * `currentLocator` StateFlow emits a second time right after the initial
     * (restored) locator is displayed — a "settle" emission carrying a
     * computed `totalProgression`/position but reflecting no user input at
     * all. If that emission is ever misread as [onUserNavigation] (echo
     * detection in `ReaderActivity` is the primary defense, but is not
     * airtight against every way Readium might emit), an [RestoreOutcome.Unresolved]
     * session must still refuse [SaveVerdict.FullSave] as long as the
     * displayed position is still start-of-book — writing spine 0 over a real
     * server position is exactly the chapter-0 data-loss bug this whole
     * redesign exists to fix. There is no per-emission signal that reliably
     * tells "the user turned a page" apart from "the navigator settled after
     * being told where to sit"; the one thing that IS trustworthy is whether
     * the displayed position actually moved off the start. A genuine forward
     * page-turn moves [atStartOfBook] to false on its own, which re-enables
     * FullSave — so this only ever suppresses the false positive, never a
     * real page-turn. [RestoreOutcome.Landed] and [RestoreOutcome.Unread] are
     * unaffected: this only ever downgrades an [RestoreOutcome.Unresolved]
     * verdict that [userNavigated] would otherwise have upgraded.
     */
    fun verdictForSave(atStartOfBook: Boolean): SaveVerdict {
        if (outcome == RestoreOutcome.Unresolved && atStartOfBook) {
            return SaveVerdict.LocalMetadataOnly
        }
        return if (outcome != RestoreOutcome.Unresolved || userNavigated) {
            SaveVerdict.FullSave
        } else {
            SaveVerdict.LocalMetadataOnly
        }
    }
}
