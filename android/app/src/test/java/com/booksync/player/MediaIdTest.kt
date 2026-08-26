package com.booksync.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The one parser for Media3 media ids (issue #141).
 *
 * The app has exactly two id shapes on the wire — "pair_N" for paired books and
 * "audiobook_N" for standalone audiobooks — and seven dispatchers that key on
 * them. The phone player used to invent a third ("standalone_N") that no
 * dispatcher recognised, so every service-side position save silently no-oped
 * for standalone playback. Everything now builds and parses through [MediaId],
 * so an unknown prefix can only come from outside and parses to null.
 */
class MediaIdTest {

    @Test
    fun `parses a pair id`() {
        assertEquals(MediaId.Pair(3), MediaId.parse("pair_3"))
    }

    @Test
    fun `parses an audiobook id`() {
        assertEquals(MediaId.Audiobook(17), MediaId.parse("audiobook_17"))
    }

    @Test
    fun `the retired standalone prefix is not a valid id`() {
        // The bug: PlayerScreen built "standalone_17" and every dispatcher
        // ignored it. The prefix must stay dead, not become a third format.
        assertNull(MediaId.parse("standalone_17"))
    }

    @Test
    fun `a prefix without a numeric id is not a valid id`() {
        assertNull(MediaId.parse("pair_"))
        assertNull(MediaId.parse("audiobook_"))
        assertNull(MediaId.parse("pair_abc"))
    }

    @Test
    fun `an empty string is not a valid id`() {
        assertNull(MediaId.parse(""))
    }

    @Test
    fun `ids round-trip through value`() {
        assertEquals("pair_3", MediaId.Pair(3).value)
        assertEquals("audiobook_17", MediaId.Audiobook(17).value)
        assertEquals(MediaId.Pair(3), MediaId.parse(MediaId.Pair(3).value))
        assertEquals(MediaId.Audiobook(17), MediaId.parse(MediaId.Audiobook(17).value))
    }
}
