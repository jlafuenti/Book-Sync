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
    private class FakeServer(
        val token: String,
        val requestedPort: Int,
        val onTokenMatchedRequest: (Long) -> Unit,
        private val failToOpen: Boolean = false,
    ) : CastFileServer {
        var opened = 0
        var closed = 0
        var readTimeoutMs = -1
        override fun open(readTimeoutMs: Int) {
            if (failToOpen) throw IOException("bind failed")
            this.readTimeoutMs = readTimeoutMs
            opened++
        }
        override fun close() { closed++ }
        // Port 0 means "any free port", as for a real socket.
        override fun boundPort(): Int = if (requestedPort == 0) 40_000 + token.length else requestedPort
    }

    private var ip: String? = "192.0.2.2"
    private val servers = mutableListOf<FakeServer>()
    private var tokenCounter = 0
    private var nextFails = false
    private var preferredPort = 8484

    /** Ports that refuse to bind; port 0 never does unless [nextFails] is set. */
    private val busyPorts = mutableSetOf<Int>()

    private fun controller() = LocalCastServerController(
        readTimeoutMs = 60_000,
        detectIp = { ip },
        preferredPort = { preferredPort },
        newServer = { token, port, onRequest ->
            FakeServer(token, port, onRequest, nextFails || port in busyPorts).also { servers += it }
        },
        newToken = { "token${++tokenCounter}" },
    )

    @Test
    fun `start boots one server at the Wi-Fi address with a fresh token`() {
        val c = controller()
        val address = c.start()
        assertEquals(CastServerAddress("192.0.2.2", 8484, "token1"), address)
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

    // --- Fixed port (issue #931) ---

    @Test
    fun `the preferred port is the one the server is asked to bind`() {
        preferredPort = 9001
        val address = controller().start()!!
        assertEquals(9001, servers.single().requestedPort)
        assertEquals(9001, address.port)
        assertFalse(address.usingFallbackPort)
    }

    @Test
    fun `a busy preferred port falls back to an ephemeral one and says so`() {
        busyPorts += 8484
        val c = controller()
        val address = c.start()!!
        assertEquals("preferred first, then any free port", listOf(8484, 0), servers.map { it.requestedPort })
        assertEquals(0, servers[0].opened)
        assertEquals(1, servers[1].opened)
        assertEquals(40_000 + "token1".length, address.port)
        assertTrue(address.usingFallbackPort)
        assertEquals(address, c.address)
    }

    @Test
    fun `both binds failing leaves nothing running`() {
        busyPorts += 8484
        // Port 0 fails too: the whole socket layer is unavailable.
        nextFails = true
        val c = controller()
        assertNull(c.start())
        assertFalse(c.isRunning)
        assertEquals("one retry, not a loop", 2, servers.size)
    }

    @Test
    fun `an ephemeral preference does not retry`() {
        preferredPort = 0
        nextFails = true
        assertNull(controller().start())
        assertEquals(1, servers.size)
    }

    @Test
    fun `the fallback keeps the same path token`() {
        busyPorts += 8484
        controller().start()
        assertEquals(servers[0].token, servers[1].token)
    }

    @Test
    fun `the port setting is read at each start, not once`() {
        val c = controller()
        c.start()
        c.stop()
        preferredPort = 9002
        val second = c.start()!!
        assertEquals(listOf(8484, 9002), servers.map { it.requestedPort })
        assertEquals(9002, second.port)
    }

    @Test
    fun `a running server is not restarted just because the setting changed`() {
        val c = controller()
        c.start()
        preferredPort = 9002
        c.start()
        assertEquals("applied at the next session", 1, servers.size)
    }

    // --- Receiver fetch notifications (issue #932) ---

    @Test
    fun `a token-matched request reaches the listener the service installed`() {
        val c = controller()
        val seen = mutableListOf<Long>()
        c.onReceiverRequest = { seen += it }
        c.start()
        servers[0].onTokenMatchedRequest(1234L)
        assertEquals(listOf(1234L), seen)
    }

    @Test
    fun `a request with no listener installed is harmless`() {
        val c = controller()
        c.start()
        servers[0].onTokenMatchedRequest(1L)
    }

    @Test
    fun `a listener installed after the server started still gets requests`() {
        val c = controller()
        c.start()
        val seen = mutableListOf<Long>()
        c.onReceiverRequest = { seen += it }
        servers[0].onTokenMatchedRequest(7L)
        assertEquals(listOf(7L), seen)
    }
}
