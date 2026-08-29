package com.booksync.data.remote

import java.io.IOException
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Issue #218. Two things were wrong with the retry policy, and both showed the
 * user something untrue.
 *
 * It exhausted its attempts on a 5xx and then threw
 * `IOException("Request failed after N attempts")`, discarding the response the
 * server had actually sent. Everything upstream treats an IOException as "no
 * network", so a server fault was reported as the user being offline.
 *
 * And the three attempts were immediate, arriving within milliseconds of each
 * other at a server already in trouble.
 */
class RetryInterceptorTest {

    private lateinit var server: MockWebServer
    private val delays = mutableListOf<Long>()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        delays.clear()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** Records backoff instead of sleeping, so the suite stays fast. */
    private fun client(maxRetries: Int = 3) = OkHttpClient.Builder()
        .addInterceptor(RetryInterceptor(maxRetries, baseDelayMs = 250) { delays += it })
        .build()

    private fun call() = client()
        .newCall(Request.Builder().url(server.url("/api/library/pairs")).build())
        .execute()

    @Test
    fun `a persistent server error is returned, not disguised as an IOException`() {
        // The bug: three 500s became "you appear to be offline".
        repeat(3) { server.enqueue(MockResponse().setResponseCode(500).setBody("boom")) }

        val response = call()

        assertEquals(500, response.code)
        assertEquals("boom", response.body?.string())
        assertEquals(3, server.requestCount)
    }

    @Test
    fun `a server error that clears is retried and succeeds`() {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))

        val response = call()

        assertEquals(200, response.code)
        assertEquals(2, server.requestCount)
        response.close()
    }

    @Test
    fun `attempts are spaced, and the spacing grows`() {
        repeat(3) { server.enqueue(MockResponse().setResponseCode(500)) }

        call().close()

        // Two gaps between three attempts, each twice the last.
        assertEquals(listOf(250L, 500L), delays)
    }

    @Test
    fun `a client error is not retried`() {
        // Retrying a 404 or a 403 cannot help and only multiplies the load.
        server.enqueue(MockResponse().setResponseCode(404))

        val response = call()

        assertEquals(404, response.code)
        assertEquals(1, server.requestCount)
        assertTrue("no backoff should have been applied", delays.isEmpty())
        response.close()
    }

    @Test
    fun `a real network failure still retries and still surfaces as an IOException`() {
        // The genuine offline case must keep behaving as it did — this is the one
        // situation where an IOException is the honest answer.
        repeat(3) {
            server.enqueue(MockResponse().apply { socketPolicy = SocketPolicy.DISCONNECT_AT_START })
        }

        var thrown: IOException? = null
        try {
            call()
        } catch (e: IOException) {
            thrown = e
        }

        assertTrue("a transport failure must still throw", thrown != null)
    }
}
