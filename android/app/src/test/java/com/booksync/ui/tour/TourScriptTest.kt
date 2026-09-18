package com.booksync.ui.tour

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins the shape of the walkthrough script (issue #597, Track A): the copy is
 * the single source of truth for the future web tour (#598), so its structure
 * has to stay predictable — every step id unique, every guided-tap or
 * wait-for-event step spotlighting a real anchor, no personal data sneaking
 * into the copy, and the Account "Replay the walkthrough" step immediately
 * before the closing "Done" card.
 */
class TourScriptTest {

    @Test
    fun `step ids are unique`() {
        val ids = TOUR.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
    }

    @Test
    fun `every TapAnchor or WaitFor step names an anchor`() {
        TOUR.forEach { step ->
            when (step.advance) {
                is Advance.TapAnchor, is Advance.WaitFor ->
                    assertTrue("step ${step.id} needs an anchor", step.anchor != null)
                Advance.Next -> Unit
            }
        }
    }

    @Test
    fun `every needsPair step has an anchor`() {
        TOUR.filter { it.needsPair }.forEach { step ->
            assertTrue("needsPair step ${step.id} needs an anchor", step.anchor != null)
        }
    }

    @Test
    fun `total step count is at most 27`() {
        assertTrue(TOUR.size <= 27)
    }

    @Test
    fun `the three tab-tap steps sit in the right places with the right anchors and events`() {
        // Issue #597 follow-up: switching bottom tabs used to be automatic; now
        // each tab switch is its own guided-tap step, like every other control
        // the tour spotlights.
        val ids = TOUR.map { it.id }
        val tapLibraryIdx = ids.indexOf("home_tap_library")
        val tapDownloadedIdx = ids.indexOf("library_tap_downloaded")
        val tapAccountIdx = ids.indexOf("downloaded_tap_account")
        assertTrue("home_tap_library is missing", tapLibraryIdx >= 0)
        assertTrue("library_tap_downloaded is missing", tapDownloadedIdx >= 0)
        assertTrue("downloaded_tap_account is missing", tapAccountIdx >= 0)

        // home_tap_library sits right after home_in_queue and right before library_open_pair.
        assertEquals(ids.indexOf("home_in_queue") + 1, tapLibraryIdx)
        assertEquals(tapLibraryIdx + 1, ids.indexOf("library_open_pair"))
        // library_tap_downloaded sits right before downloaded_pills.
        assertEquals(tapDownloadedIdx + 1, ids.indexOf("downloaded_pills"))
        // downloaded_tap_account sits right before account_storage_and_server.
        assertEquals(tapAccountIdx + 1, ids.indexOf("account_storage_and_server"))

        val tapLibrary = TOUR[tapLibraryIdx]
        assertEquals(TourScreen.Home, tapLibrary.screen)
        assertEquals(TourAnchor.TabLibrary, tapLibrary.anchor)
        assertEquals(Advance.TapAnchor(TourEvent.RouteShown("library")), tapLibrary.advance)
        assertFalse(tapLibrary.needsPair)

        val tapDownloaded = TOUR[tapDownloadedIdx]
        assertEquals(TourScreen.Library, tapDownloaded.screen)
        assertEquals(TourAnchor.TabDownloaded, tapDownloaded.anchor)
        assertEquals(Advance.TapAnchor(TourEvent.RouteShown("downloaded")), tapDownloaded.advance)
        assertFalse(tapDownloaded.needsPair)

        val tapAccount = TOUR[tapAccountIdx]
        assertEquals(TourScreen.Downloaded, tapAccount.screen)
        assertEquals(TourAnchor.TabAccount, tapAccount.anchor)
        assertEquals(Advance.TapAnchor(TourEvent.RouteShown("account")), tapAccount.advance)
        assertFalse(tapAccount.needsPair)
    }

    @Test
    fun `the Account replay step is the last real step, followed by a closing Done card`() {
        val last = TOUR.last()
        val secondToLast = TOUR[TOUR.size - 2]
        assertEquals(TourAnchor.AccountReplayTour, secondToLast.anchor)
        assertEquals(TourScreen.Account, secondToLast.screen)
        // The closing card has nothing left to spotlight or wait for.
        assertEquals(null, last.anchor)
        assertEquals(Advance.Next, last.advance)
    }

    @Test
    fun `copy contains no obvious personal data`() {
        val forbidden = listOf("lafuenti", "tandem.local", "@gmail.com", "192.168.", "10.0.")
        TOUR.forEach { step ->
            val text = (step.title + " " + step.body + " " + (step.emptyBody ?: "")).lowercase()
            forbidden.forEach { needle ->
                assertTrue("step ${step.id} body leaks '$needle'", !text.contains(needle))
            }
        }
    }

    @Test
    fun `copy defines sync map before assuming it`() {
        // The first step whose body mentions "sync map" is the one that defines it —
        // a plain-text sanity check that we didn't drop the definition.
        val firstMention = TOUR.indexOfFirst { it.body.contains("sync map", ignoreCase = true) }
        assertTrue(firstMention >= 0)
        assertTrue(
            TOUR[firstMention].body.contains("sentence", ignoreCase = true) ||
                TOUR[firstMention].body.contains("link", ignoreCase = true),
        )
    }
}
