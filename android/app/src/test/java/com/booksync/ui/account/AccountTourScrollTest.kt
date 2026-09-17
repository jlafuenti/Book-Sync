package com.booksync.ui.account

import com.booksync.ui.tour.TourAnchor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The Account list is lazy, so a walkthrough step scrolls to its section's
 * title before spotlighting it (issue #597). The indices follow the fixed
 * item order in `AccountScreen`: user card, then title/content pairs.
 */
class AccountTourScrollTest {

    @Test
    fun `each Account anchor maps to its section title`() {
        assertEquals(3, accountSectionIndex(TourAnchor.AccountStorage))
        assertEquals(11, accountSectionIndex(TourAnchor.AccountServer))
        assertEquals(13, accountSectionIndex(TourAnchor.AccountReplayTour))
    }

    @Test
    fun `anchors from other screens are not on this list`() {
        assertNull(accountSectionIndex(TourAnchor.HomeInQueue))
        assertNull(accountSectionIndex(TourAnchor.DetailsUnlink))
    }
}
