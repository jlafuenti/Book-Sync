package com.booksync.data.remote

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wikimedia refuses a bare library default agent string with 403 — see
 * [wiktionaryUserAgent]. These pin the shape its robot policy asks for:
 * the application, its version, and a way to make contact.
 */
class WiktionaryUserAgentTest {

    @Test
    fun `names the app and the version it was built from`() {
        val agent = wiktionaryUserAgent("0.4.0")

        assertTrue(agent, agent.startsWith("Tandem/0.4.0"))
    }

    @Test
    fun `offers a way to make contact`() {
        val agent = wiktionaryUserAgent("0.4.0")

        assertTrue(agent, agent.contains("tandembook.com"))
        assertTrue(agent, agent.contains("support@tandembook.com"))
    }

    @Test
    fun `is not a library default`() {
        val agent = wiktionaryUserAgent("0.4.0")

        assertFalse(agent, agent.contains("okhttp", ignoreCase = true))
        assertFalse(agent, agent.isBlank())
    }
}
