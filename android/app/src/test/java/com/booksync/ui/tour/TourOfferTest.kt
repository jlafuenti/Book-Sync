package com.booksync.ui.tour

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The pure decision behind the first-sign-in "Take the 5-minute tour?" dialog (issue #597 §5).
 *
 * [offered] is `Boolean?` rather than `Boolean` (issue #642): DataStore hasn't necessarily
 * answered by the time this is first evaluated, and treating that "don't know yet" moment as
 * `false` flashed the dialog for a returning user who had, in fact, already answered it.
 * `null` — unknown — must never offer.
 */
class TourOfferTest {

    @Test
    fun `offers when signed in and known not yet offered`() {
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

    @Test
    fun `unknown never offers, even when signed in`() {
        assertFalse(shouldOfferTour(offered = null, signedIn = true))
    }

    @Test
    fun `unknown and signed out does not offer`() {
        assertFalse(shouldOfferTour(offered = null, signedIn = false))
    }
}
