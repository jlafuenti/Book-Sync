package com.booksync.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #58: the shipped server URL moved out of the source and into a build
 * property that defaults to empty, so a clean clone no longer points at anyone's
 * personal host. Two things have to hold for a build with no default:
 *
 *  - Retrofit still constructs (it rejects an empty base URL outright), and
 *  - the login screen opens with its "Advanced" server-URL field already visible,
 *    otherwise there is no discoverable way to enter one.
 */
class ServerUrlPolicyTest {

    @Test
    fun `blank stored url falls back to a constructible placeholder`() {
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl(""))
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl("   "))
        // Whatever the placeholder is, Retrofit requires a trailing slash.
        assertTrue(UNCONFIGURED_BASE_URL.endsWith("/"))
    }

    @Test
    fun `a configured url is trailing-slashed exactly once`() {
        assertEquals("https://host/", retrofitBaseUrl("https://host"))
        assertEquals("https://host/", retrofitBaseUrl("https://host/"))
        assertEquals("https://host:8443/", retrofitBaseUrl("https://host:8443///"))
    }

    @Test
    fun `advanced section starts expanded only when no server is configured`() {
        assertTrue(shouldExpandAdvanced(""))
        assertTrue(shouldExpandAdvanced("   "))
        assertFalse(shouldExpandAdvanced("https://host"))
    }
}
