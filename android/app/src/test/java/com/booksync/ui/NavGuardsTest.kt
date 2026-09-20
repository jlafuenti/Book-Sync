package com.booksync.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The reader is a second Activity, so "the reader closed" reaches the nav host late, as an
 * activity result (issue #642 follow-up). By then the walkthrough may already have popped
 * the reader's route on its own — and an unconditional `popBackStack()` from that late
 * callback then popped `MAIN`, the only entry left, leaving a blank app that not even
 * quitting the tour recovered.
 */
class NavGuardsTest {

    @Test
    fun `a route pops while it is the current destination`() {
        assertTrue(shouldPopRoute(Routes.READER, currentRoute = Routes.READER))
    }

    @Test
    fun `a late pop does nothing once something else is current`() {
        assertFalse(shouldPopRoute(Routes.READER, currentRoute = Routes.MAIN))
        assertFalse(shouldPopRoute(Routes.READER_STANDALONE, currentRoute = Routes.MAIN))
    }

    @Test
    fun `a late pop does nothing when the back stack is already empty`() {
        assertFalse(shouldPopRoute(Routes.READER, currentRoute = null))
    }
}
