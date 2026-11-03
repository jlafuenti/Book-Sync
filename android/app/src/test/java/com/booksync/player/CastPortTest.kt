package com.booksync.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The cast port's rules and the two lines the Account screen shows about it (issue #931). */
class CastPortTest {

    @Test
    fun `the default port is 8484`() {
        assertEquals(8484, DEFAULT_CAST_PORT)
        assertTrue(isValidCastPort(DEFAULT_CAST_PORT))
    }

    @Test
    fun `ports 1024 to 65535 are valid and nothing outside them is`() {
        assertTrue(isValidCastPort(1024))
        assertTrue(isValidCastPort(65535))
        assertFalse(isValidCastPort(1023))
        assertFalse(isValidCastPort(65536))
        assertFalse(isValidCastPort(0))
        assertFalse(isValidCastPort(-1))
    }

    @Test
    fun `typed text is parsed, trimmed and range-checked`() {
        assertEquals(9000, parseCastPort("9000"))
        assertEquals(9000, parseCastPort("  9000 "))
        assertNull(parseCastPort(""))
        assertNull(parseCastPort("abc"))
        assertNull(parseCastPort("80"))
        assertNull(parseCastPort("70000"))
        assertNull(parseCastPort("99999999999"))
        assertNull(parseCastPort("84.84"))
    }

    @Test
    fun `the address line names the Wi-Fi address and port a firewall rule needs`() {
        assertEquals("Speakers fetch from http://192.0.2.5:8484/", castAddressLine("192.0.2.5", 8484))
    }

    @Test
    fun `the address line says so when there is no Wi-Fi`() {
        assertEquals("Speakers fetch from: no Wi-Fi connection", castAddressLine(null, 8484))
    }

    @Test
    fun `the fallback line names both ports`() {
        assertEquals("Using port 41234 because 8484 was busy.", castFallbackLine(41234, 8484))
    }
}
