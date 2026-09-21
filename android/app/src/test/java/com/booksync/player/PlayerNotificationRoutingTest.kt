package com.booksync.player

import com.booksync.ui.Routes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * [openPlayerRouteFor] is the pure routing decision behind issue #684: a tap
 * on the playback notification's cover art or body carries a media id, and
 * this is what turns that id into a navigation route. It goes through
 * [MediaId.parse] rather than its own string checks for the same reason
 * every other dispatcher does (see [MediaIdWiringTest]) — a third
 * home-grown parser is how issue #141 happened.
 */
class PlayerNotificationRoutingTest {

    @Test
    fun `a pair media id routes to that pair's player`() {
        assertEquals(Routes.player(42), openPlayerRouteFor("pair_42"))
    }

    @Test
    fun `a standalone audiobook media id routes to the standalone player`() {
        assertEquals(Routes.playerStandalone(7), openPlayerRouteFor("audiobook_7"))
    }

    @Test
    fun `an unparseable media id routes nowhere`() {
        assertNull(openPlayerRouteFor("standalone_9"))
        assertNull(openPlayerRouteFor("garbage"))
        assertNull(openPlayerRouteFor(""))
    }

    @Test
    fun `a null media id routes nowhere`() {
        assertNull(openPlayerRouteFor(null))
    }
}
