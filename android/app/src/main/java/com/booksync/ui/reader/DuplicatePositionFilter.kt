package com.booksync.ui.reader

/**
 * Drops a position write that is identical to the one immediately before it.
 *
 * Every exit from the reader used to write the same position twice:
 * `switchToAudio`, the toolbar back button and the home menu item all call
 * `saveCurrentPosition()` and then `finish()`, and `finish()` runs `onPause()`,
 * which calls `saveCurrentPosition()` again. On a device that showed up as two
 * `pending_sync` rows 6ms apart carrying a byte-identical position.
 *
 * Filtering here rather than deleting the pre-`finish()` saves is deliberate:
 * those capture the snapshot synchronously so a fast close cannot cancel it
 * mid-flight (see `savePosition`'s issue #61/#40 fix 2 comment), and `onPause`
 * has to keep saving for the exits that never go through an explicit call —
 * the home gesture, the screen turning off, another app taking focus. Only the
 * redundant repeat goes away, and it goes away for every exit path at once.
 *
 * Comparison is against the previous *accepted* write only, so turning back to
 * a page already visited is still recorded — that is a real move.
 *
 * Not thread-safe: `savePosition` only ever runs on the main thread.
 */
class DuplicatePositionFilter {

    private var lastWrittenKey: String? = null

    /**
     * Whether [key] should be written. Records it as the new baseline when the
     * answer is yes — a refused write must not become the baseline, or the
     * position it duplicated would be forgotten.
     */
    fun shouldWrite(key: String): Boolean {
        if (key == lastWrittenKey) return false
        lastWrittenKey = key
        return true
    }
}
