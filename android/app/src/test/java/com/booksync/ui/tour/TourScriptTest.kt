package com.booksync.ui.tour

import org.junit.Assert.assertEquals
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
    fun `total step count is at most 24`() {
        assertTrue(TOUR.size <= 24)
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
