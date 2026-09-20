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
    fun `copy defines sync map within its first two mentions`() {
        // details_chips (issue #597 tester feedback) name-drops "sync map" in passing —
        // "the sync map downloads itself the first time you open the book" — and the very
        // next step, details_sync_map, is what actually defines it. Two mentions before the
        // definition is fine; the point is that the copy doesn't assume the term for long.
        val mentions = TOUR.filter { it.body.contains("sync map", ignoreCase = true) }
        assertTrue(mentions.isNotEmpty())
        val defines = mentions.take(2).any {
            it.body.contains("sentence", ignoreCase = true) || it.body.contains("link", ignoreCase = true)
        }
        assertTrue("sync map should be defined within its first two mentions", defines)
    }

    @Test
    fun `home_welcome explains the tour puts the book it used back the way it was`() {
        // Issue #597 tester feedback: the old copy promised no trace at all, but the pair
        // the tour opened kept showing up in Continue Reading and Downloaded afterward.
        val step = TOUR.first { it.id == "home_welcome" }
        assertTrue(step.body.contains("put back the way it was"))
    }

    @Test
    fun `details_chips explains green, grey, and the sync map downloading itself`() {
        val step = TOUR.first { it.id == "details_chips" }
        assertTrue(step.body.contains("green chip"))
        assertTrue(step.body.contains("Grey"))
        assertTrue(step.body.contains("sync map downloads itself"))
    }

    @Test
    fun `reader_select_sentence explains how much to select`() {
        val step = TOUR.first { it.id == READER_SELECTION_STEP_ID }
        assertTrue(step.body.contains("at least a few words"))
        assertTrue(step.body.contains("Sync to Audio"))
    }

    @Test
    fun `details_maintenance accepts the Refresh sync data row as an alternate anchor`() {
        // Issue #642: a viewer with a sync map on the device sees "Refresh sync
        // data" even though Unlink pair (editor-only) is absent, so the step
        // must not treat that as "neither row shown".
        val step = TOUR.first { it.id == "details_maintenance" }
        assertEquals(listOf(TourAnchor.DetailsRefreshSync), step.altAnchors)
    }
}
