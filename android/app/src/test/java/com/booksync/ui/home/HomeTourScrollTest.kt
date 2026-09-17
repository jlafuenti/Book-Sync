package com.booksync.ui.home

import com.booksync.ui.tour.TourAnchor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Home scrolls a section into view before the walkthrough spotlights it
 * (issue #597): a `LazyColumn` lays out nothing below the fold, so an
 * un-scrolled section has no anchor and the tour would call it empty.
 */
class HomeTourScrollTest {

    @Test
    fun `finds the item index of a section that is present`() {
        val sections = listOf(null, TourAnchor.HomeContinueReading, TourAnchor.HomeRecentlyAdded, null, TourAnchor.HomeInQueue)
        assertEquals(1, homeSectionIndex(TourAnchor.HomeContinueReading, sections))
        assertEquals(4, homeSectionIndex(TourAnchor.HomeInQueue, sections))
    }

    @Test
    fun `an absent section has no index`() {
        val sections = listOf(TourAnchor.HomeContinueReading, TourAnchor.HomeRecentlyAdded)
        assertNull(homeSectionIndex(TourAnchor.HomeInQueue, sections))
    }

    @Test
    fun `the index follows the order sections are emitted in`() {
        // Without the banner and Continue Reading, Recently Added is the first item.
        val sections = listOf(TourAnchor.HomeRecentlyAdded, null, TourAnchor.HomeInQueue)
        assertEquals(0, homeSectionIndex(TourAnchor.HomeRecentlyAdded, sections))
        assertEquals(2, homeSectionIndex(TourAnchor.HomeInQueue, sections))
    }
}
