package com.booksync.player

import fi.iki.elonen.NanoHTTPD
import io.mockk.every
import io.mockk.mockk
import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The LAN server's request handling (issue #225), driven through
 * `NanoHTTPD.serve` with a mocked session — no socket is opened. What matters
 * is exactly what a Cast receiver depends on: the path token gate, path
 * containment, and byte-exact Range responses (a moov-at-end M4B is unplayable
 * if the bytes delivered do not match the declared Content-Range).
 */
class LocalCastHttpServerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val token = "0123456789abcdef"
    private val body = ByteArray(100) { it.toByte() }

    private fun server(): LocalCastHttpServer {
        File(tmp.root, "book.mp3").writeBytes(body)
        return LocalCastHttpServer(audiobooksDir = tmp.root, pathToken = token)
    }

    /**
     * The bytes the receiver would get. A fixed-length response only cuts the
     * body at its declared length when it is written to the socket, so read
     * exactly that many here — that is what makes the Content-Range assertions
     * byte-exact rather than "starts in the right place".
     */
    private fun body(response: NanoHTTPD.Response, length: Int): ByteArray =
        response.data.readNBytes(length)

    private fun request(uri: String, range: String? = null): NanoHTTPD.IHTTPSession {
        val session = mockk<NanoHTTPD.IHTTPSession>()
        every { session.uri } returns uri
        every { session.method } returns NanoHTTPD.Method.GET
        every { session.headers } returns if (range == null) emptyMap() else mapOf("range" to range)
        return session
    }

    // --- Gate ---

    @Test
    fun `a request without the session token is not found`() {
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, server().serve(request("/wrongtoken/book.mp3")).status)
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, server().serve(request("/book.mp3")).status)
    }

    @Test
    fun `path traversal and nested paths are refused`() {
        val s = server()
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, s.serve(request("/$token/../book.mp3")).status)
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, s.serve(request("/$token/sub/book.mp3")).status)
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, s.serve(request("/$token/")).status)
    }

    @Test
    fun `a file that is not downloaded is not found`() {
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, server().serve(request("/$token/missing.mp3")).status)
    }

    // --- Full body ---

    @Test
    fun `a plain GET serves the whole file with its mime type and Accept-Ranges`() {
        val response = server().serve(request("/$token/book.mp3"))
        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        assertEquals("audio/mpeg", response.mimeType)
        assertEquals("bytes", response.getHeader("Accept-Ranges"))
        assertArrayEquals(body, body(response, 100))
    }

    // --- Ranges ---

    @Test
    fun `a closed range delivers exactly the bytes it declares`() {
        val response = server().serve(request("/$token/book.mp3", range = "bytes=10-19"))
        assertEquals(NanoHTTPD.Response.Status.PARTIAL_CONTENT, response.status)
        assertEquals("bytes 10-19/100", response.getHeader("Content-Range"))
        assertArrayEquals(body.copyOfRange(10, 20), body(response, 10))
    }

    @Test
    fun `an open-ended range runs to the end of the file`() {
        // The receiver's first request is `bytes=0-`; a moov-at-end M4B then
        // asks for the tail before it can decode anything.
        val response = server().serve(request("/$token/book.mp3", range = "bytes=90-"))
        assertEquals(NanoHTTPD.Response.Status.PARTIAL_CONTENT, response.status)
        assertEquals("bytes 90-99/100", response.getHeader("Content-Range"))
        assertArrayEquals(body.copyOfRange(90, 100), body(response, 10))
    }

    @Test
    fun `an end past the file is clamped`() {
        val response = server().serve(request("/$token/book.mp3", range = "bytes=95-500"))
        assertEquals("bytes 95-99/100", response.getHeader("Content-Range"))
        assertArrayEquals(body.copyOfRange(95, 100), body(response, 5))
    }

    @Test
    fun `a start past the file or an inverted range is not satisfiable`() {
        val s = server()
        for (range in listOf("bytes=100-", "bytes=50-10", "bytes=abc")) {
            val response = s.serve(request("/$token/book.mp3", range = range))
            assertEquals(range, NanoHTTPD.Response.Status.RANGE_NOT_SATISFIABLE, response.status)
            assertEquals("bytes */100", response.getHeader("Content-Range"))
        }
    }

    @Test
    fun `an unknown extension is served as an octet stream`() {
        File(tmp.root, "book.xyz").writeBytes(body)
        val response = LocalCastHttpServer(tmp.root, token).serve(request("/$token/book.xyz"))
        assertEquals("application/octet-stream", response.mimeType)
        assertNull(response.getHeader("Content-Range"))
    }
}
