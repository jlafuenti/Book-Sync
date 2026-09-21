package com.booksync.player

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * [PendingPlayerNavigation] carries a route from a notification tap
 * (issue #684), handled by `MainActivity`, across to `BookSyncNavigation`,
 * which owns the `NavController`. Two rules matter enough to pin here
 * rather than trust to the wiring alone:
 *
 * - **Consumed once.** A recomposition or a second reader must not replay
 *   the same navigation a second time.
 * - **Dropped when signed out**, not queued. A tap that arrives while the
 *   app is signed out (or mid-onboarding) should open the app normally, not
 *   surprise-navigate the moment the user later signs in.
 */
class PendingPlayerNavigationTest {

    @After
    fun tearDown() {
        PendingPlayerNavigation.clearForTest()
    }

    @Test
    fun `a pending route is handed to a signed-in consumer, and only once`() {
        PendingPlayerNavigation.set("player/42")

        assertEquals("player/42", PendingPlayerNavigation.consume(signedIn = true))
        assertNull(PendingPlayerNavigation.consume(signedIn = true))
    }

    @Test
    fun `a pending route is dropped, not queued, for a signed-out consumer`() {
        PendingPlayerNavigation.set("player/42")

        assertNull(PendingPlayerNavigation.consume(signedIn = false))
        // Dropped outright: signing in afterwards must not replay it.
        assertNull(PendingPlayerNavigation.consume(signedIn = true))
    }

    @Test
    fun `nothing pending consumes to null`() {
        assertNull(PendingPlayerNavigation.consume(signedIn = true))
    }

    @Test
    fun `setting a new route overwrites one that was never consumed`() {
        PendingPlayerNavigation.set("player/1")
        PendingPlayerNavigation.set("player/2")

        assertEquals("player/2", PendingPlayerNavigation.consume(signedIn = true))
    }
}
