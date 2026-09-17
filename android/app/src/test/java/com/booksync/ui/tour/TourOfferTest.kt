package com.booksync.ui.tour

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The pure decision behind the first-sign-in "Take the 5-minute tour?" dialog (issue #597 §5). */
class TourOfferTest {

    @Test
    fun `offers when signed in and never offered before`() {
        assertTrue(shouldOfferTour(offered = false, signedIn = true))
    }

    @Test
    fun `does not offer twice`() {
        assertFalse(shouldOfferTour(offered = true, signedIn = true))
    }

    @Test
    fun `does not offer while signed out`() {
        assertFalse(shouldOfferTour(offered = false, signedIn = false))
    }

    @Test
    fun `signed out and already offered still does not offer`() {
        assertFalse(shouldOfferTour(offered = true, signedIn = false))
    }
}
