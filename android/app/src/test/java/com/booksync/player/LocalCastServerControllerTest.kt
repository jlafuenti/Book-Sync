package com.booksync.player

import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Lifecycle of the phone's LAN cast server (issue #225): when it is started,
 * when it is left alone, when it is restarted, and that each start gets a
 * fresh path token. `AudioPlayerService` used to own this as four nullable
 * fields and two methods, tested only by casting to a real speaker.
 */
class LocalCastServerControllerTest {

    /** A server that records its lifecycle instead of opening a socket. */
    private class FakeServer(val token: String, private val failToOpen: Boolean = false) : CastFileServer {
        var opened = 0
        var closed = 0
        var readTimeoutMs = -1
        override fun open(readTimeoutMs: Int) {
            if (failToOpen) throw IOException("bind failed")
            this.readTimeoutMs = readTimeoutMs
            opened++
        }
        override fun close() { closed++ }
        override fun boundPort(): Int = 40_000 + token.length
    }

    private var ip: String? = "192.0.2.2"
    private val servers = mutableListOf<FakeServer>()
    private var tokenCounter = 0
    private var nextFails = false

    private fun controller() = LocalCastServerController(
        readTimeoutMs = 60_000,
        detectIp = { ip },
        newServer = { token -> FakeServer(token, nextFails).also { servers += it } },
        newToken = { "token${++tokenCounter}" },
    )

    @Test
    fun `start boots one server at the Wi-Fi address with a fresh token`() {
        val c = controller()
        val address = c.start()
        assertEquals(CastServerAddress("192.0.2.2", 40_006, "token1"), address)
        assertEquals(address, c.address)
        assertEquals(1, servers.size)
        assertEquals(1, servers[0].opened)
        assertEquals(60_000, servers[0].readTimeoutMs)
    }

    @Test
    fun `starting again on the same address leaves the running server alone`() {
        val c = controller()
        val first = c.start()
        val second = c.start()
        assertEquals(first, second)
        assertEquals("a second server must not be started", 1, servers.size)
        assertEquals(0, servers[0].closed)
    }

    @Test
    fun `a changed Wi-Fi address restarts the server on the new one with a new token`() {
        val c = controller()
        val first = c.start()
        ip = "192.0.2.9"
        val second = c.start()
        assertEquals(2, servers.size)
        assertEquals("the old server is torn down", 1, servers[0].closed)
        assertEquals("192.0.2.9", second!!.ip)
        assertNotEquals(first!!.pathToken, second.pathToken)
        assertEquals(second, c.address)
    }

    @Test
    fun `no Wi-Fi address means no server, and tears down one that was running`() {
        val c = controller()
        c.start()
        ip = null
        assertNull(c.start())
        assertNull(c.address)
        assertFalse(c.isRunning)
        assertEquals(1, servers[0].closed)
    }

    @Test
    fun `a server that fails to bind leaves nothing running`() {
        nextFails = true
        val c = controller()
        assertNull(c.start())
        assertNull(c.address)
        assertFalse(c.isRunning)
    }

    @Test
    fun `stop is idempotent and clears the address`() {
        val c = controller()
        c.start()
        c.stop()
        c.stop()
        assertNull(c.address)
        assertEquals(1, servers[0].closed)
    }

    @Test
    fun `every start after a stop gets a new token`() {
        val c = controller()
        val a = c.start()!!.pathToken
        c.stop()
        val b = c.start()!!.pathToken
        assertNotEquals(a, b)
        assertTrue(servers.map { it.token }.containsAll(listOf(a, b)))
    }
}
