package com.booksync.ui.reader

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Drops a position write that is byte-identical to the one just made.
 *
 * The bug this pins: every exit from the reader wrote the same position twice.
 * `switchToAudio`, the toolbar back button and the home button all call
 * `saveCurrentPosition()` and then `finish()`, and `finish()` runs `onPause()`,
 * which calls `saveCurrentPosition()` again. Observed on a device as two
 * `pending_sync` rows 6ms apart carrying an identical position.
 *
 * Deleting the explicit pre-`finish()` saves would also fix it, but those exist
 * deliberately — the capture is synchronous specifically so a fast close cannot
 * cancel it (see `savePosition`'s comment about issue #61/#40 fix 2). Filtering
 * the redundant repeat keeps that safety net and fixes the back and home exits
 * at the same time, not just the audio switch.
 */
class DuplicatePositionFilterTest {

    @Test
    fun `the first write of a position is allowed`() {
        assertTrue(DuplicatePositionFilter().shouldWrite("ch1@0.25"))
    }

    @Test
    fun `an immediate repeat of the same position is dropped`() {
        val filter = DuplicatePositionFilter()
        assertTrue(filter.shouldWrite("ch1@0.25"))
        assertFalse(filter.shouldWrite("ch1@0.25"))
        assertFalse(filter.shouldWrite("ch1@0.25"))
    }

    @Test
    fun `a different position is allowed`() {
        val filter = DuplicatePositionFilter()
        assertTrue(filter.shouldWrite("ch1@0.25"))
        assertTrue(filter.shouldWrite("ch1@0.30"))
        assertTrue(filter.shouldWrite("ch2@0.00"))
    }

    @Test
    fun `returning to an earlier position is allowed`() {
        // Only the immediately preceding write is compared. Turning back to a
        // page you already saved is a real move and must still be recorded —
        // otherwise the reader could not go back a page and have it stick.
        val filter = DuplicatePositionFilter()
        assertTrue(filter.shouldWrite("ch1@0.25"))
        assertTrue(filter.shouldWrite("ch2@0.10"))
        assertTrue(filter.shouldWrite("ch1@0.25"))
    }

    @Test
    fun `a dropped repeat does not become the new baseline`() {
        // Guards the obvious implementation slip of recording the key even
        // when the write was refused: after two identical calls, a third
        // distinct position must still be allowed.
        val filter = DuplicatePositionFilter()
        assertTrue(filter.shouldWrite("ch1@0.25"))
        assertFalse(filter.shouldWrite("ch1@0.25"))
        assertTrue(filter.shouldWrite("ch3@0.90"))
        assertFalse(filter.shouldWrite("ch3@0.90"))
    }
}
