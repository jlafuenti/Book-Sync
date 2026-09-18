package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Issue #585: tapping near the left/right edge of the reader page turns the
 * page, as an alternative to a full swipe. [decideReaderTapAction] is the
 * pure decision half — given where a tap landed (as a fraction of the page
 * width), the book's live reading direction and the edge-tap setting, what
 * should happen: turn to the previous or next page, or fall through to the
 * existing "toggle the toolbar" behavior.
 */
class ReaderTapZoneTest {

    // ======== LTR (the common case) ========

    @Test
    fun `a tap in the left edge zone turns to the previous page in LTR`() {
        val action = decideReaderTapAction(xFraction = 0.05, isRtl = false, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.TurnPageBackward, action)
    }

    @Test
    fun `a tap in the right edge zone turns to the next page in LTR`() {
        val action = decideReaderTapAction(xFraction = 0.95, isRtl = false, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.TurnPageForward, action)
    }

    @Test
    fun `a tap in the middle toggles the bars in LTR`() {
        val action = decideReaderTapAction(xFraction = 0.5, isRtl = false, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.ToggleBars, action)
    }

    // ======== RTL books swap sides ========

    @Test
    fun `a tap in the left edge zone turns to the next page in RTL`() {
        val action = decideReaderTapAction(xFraction = 0.05, isRtl = true, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.TurnPageForward, action)
    }

    @Test
    fun `a tap in the right edge zone turns to the previous page in RTL`() {
        val action = decideReaderTapAction(xFraction = 0.95, isRtl = true, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.TurnPageBackward, action)
    }

    @Test
    fun `a tap in the middle toggles the bars in RTL too`() {
        val action = decideReaderTapAction(xFraction = 0.5, isRtl = true, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.ToggleBars, action)
    }

    // ======== Zone boundaries (default 22% each side) ========

    @Test
    fun `just inside the left edge zone still turns the page`() {
        val action = decideReaderTapAction(xFraction = 0.21, isRtl = false, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.TurnPageBackward, action)
    }

    @Test
    fun `exactly on the edge-zone boundary counts as the middle`() {
        // xFraction == edgeFraction is the first fraction that is NOT "near
        // enough" to the edge — the comparison is strict, so the boundary
        // itself belongs to the wide middle zone.
        val action = decideReaderTapAction(xFraction = READER_TAP_EDGE_FRACTION, isRtl = false, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.ToggleBars, action)
    }

    @Test
    fun `just inside the right edge zone still turns the page`() {
        val action = decideReaderTapAction(xFraction = 0.79, isRtl = false, edgeTapEnabled = true)
        assertEquals(ReaderTapAction.TurnPageForward, action)
    }

    @Test
    fun `a custom edge fraction widens the zone`() {
        // 0.30 in from each side — a tap at 0.25 is in the edge zone only
        // with the wider setting.
        val default = decideReaderTapAction(xFraction = 0.25, isRtl = false, edgeTapEnabled = true)
        val widened = decideReaderTapAction(xFraction = 0.25, isRtl = false, edgeTapEnabled = true, edgeFraction = 0.30)
        assertEquals(ReaderTapAction.ToggleBars, default)
        assertEquals(ReaderTapAction.TurnPageBackward, widened)
    }

    // ======== The "disable edge taps" setting ========

    @Test
    fun `a disabled setting makes an edge tap toggle the bars instead`() {
        val leftEdge = decideReaderTapAction(xFraction = 0.05, isRtl = false, edgeTapEnabled = false)
        val rightEdge = decideReaderTapAction(xFraction = 0.95, isRtl = false, edgeTapEnabled = false)
        assertEquals(ReaderTapAction.ToggleBars, leftEdge)
        assertEquals(ReaderTapAction.ToggleBars, rightEdge)
    }

    @Test
    fun `a disabled setting still toggles the bars in the middle`() {
        val action = decideReaderTapAction(xFraction = 0.5, isRtl = false, edgeTapEnabled = false)
        assertEquals(ReaderTapAction.ToggleBars, action)
    }

    // ======== Regression guard for the tour's "tap the middle" step ========

    /**
     * `reader_tap_page` in `TourScript.kt` spotlights the whole page and
     * waits for [com.booksync.ui.tour.TourEvent.ReaderBarsShown] before
     * advancing, which only fires from [ReaderActivity.setBarsVisible]. A
     * user following the step's "tap the middle of the page" instruction
     * must still get [ReaderTapAction.ToggleBars] — in every combination of
     * reading direction and the edge-tap setting — or the step stalls until
     * the tour's own timeout escape hatch (`TourNav.ShowReaderBars`) kicks
     * in instead of responding to the tap.
     */
    @Test
    fun `a middle tap always toggles the bars, keeping the tour's middle-tap step working`() {
        for (isRtl in listOf(false, true)) {
            for (edgeTapEnabled in listOf(false, true)) {
                val action = decideReaderTapAction(xFraction = 0.5, isRtl = isRtl, edgeTapEnabled = edgeTapEnabled)
                assertEquals(
                    "isRtl=$isRtl edgeTapEnabled=$edgeTapEnabled",
                    ReaderTapAction.ToggleBars,
                    action,
                )
            }
        }
    }
}
