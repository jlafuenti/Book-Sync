package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Issue #597 follow-up. Tester feedback on the guided walkthrough: the "sync a
 * sentence" step opened the book on the cover, where there is nothing to
 * select. [tourJumpTarget] is the pure decision behind the fix — see its doc
 * for the three conditions — exercised here as a table of every combination
 * that matters, so the "never move a real reader's place" guarantee is pinned
 * as data rather than prose.
 */
class TourReaderJumpTest {

    private data class Case(
        val name: String,
        val tourRunningOnReader: Boolean,
        val tourPairId: Int?,
        val thisPairId: Int,
        val hasSavedPosition: Boolean,
        val expected: Double?,
    )

    private val cases = listOf(
        Case(
            name = "tour on a Reader step, same pair, no saved position -> jumps",
            tourRunningOnReader = true,
            tourPairId = 42,
            thisPairId = 42,
            hasSavedPosition = false,
            expected = 0.05,
        ),
        Case(
            name = "tour not running on the reader at all -> no jump",
            tourRunningOnReader = false,
            tourPairId = 42,
            thisPairId = 42,
            hasSavedPosition = false,
            expected = null,
        ),
        Case(
            name = "tour running elsewhere, pair id happens to match -> still no jump",
            tourRunningOnReader = false,
            tourPairId = null,
            thisPairId = 42,
            hasSavedPosition = false,
            expected = null,
        ),
        Case(
            name = "tour on a Reader step but for a different pair -> no jump",
            tourRunningOnReader = true,
            tourPairId = 7,
            thisPairId = 42,
            hasSavedPosition = false,
            expected = null,
        ),
        Case(
            name = "tour has no pair at all (degraded picker) -> no jump",
            tourRunningOnReader = true,
            tourPairId = null,
            thisPairId = 42,
            hasSavedPosition = false,
            expected = null,
        ),
        Case(
            name = "same pair on a Reader step, but a real saved position exists -> never moved",
            tourRunningOnReader = true,
            tourPairId = 42,
            thisPairId = 42,
            hasSavedPosition = true,
            expected = null,
        ),
        Case(
            name = "not running on the reader AND a saved position exists -> still no jump",
            tourRunningOnReader = false,
            tourPairId = 42,
            thisPairId = 42,
            hasSavedPosition = true,
            expected = null,
        ),
        Case(
            name = "standalone ebook (pair id 0) never coincides with a real tour pair id",
            tourRunningOnReader = true,
            tourPairId = 0,
            thisPairId = 0,
            hasSavedPosition = false,
            // Same pair id (0) and every other condition met: still jumps —
            // this case only documents that a standalone open (thisPairId
            // always 0) can never accidentally match a tour that legitimately
            // has no pair (tourPairId == null), which is covered above.
            expected = 0.05,
        ),
    )

    @Test
    fun `tourJumpTarget over the decision table`() {
        for (case in cases) {
            val actual = tourJumpTarget(
                tourRunningOnReader = case.tourRunningOnReader,
                tourPairId = case.tourPairId,
                thisPairId = case.thisPairId,
                hasSavedPosition = case.hasSavedPosition,
            )
            assertEquals(case.name, case.expected, actual)
        }
    }
}
