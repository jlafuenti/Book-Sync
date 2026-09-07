package com.booksync.data.remote

import java.io.IOException
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The edges of [RetryInterceptor]'s budget (issue #217), alongside the retry
 * policy itself in [RetryInterceptorTest]: a budget of one attempt, a budget of
 * none, and a call that is cancelled while it is between attempts.
 */
class RetryInterceptorBoundsTest {

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

    private fun request() = Request.Builder().url(server.url("/api/library/pairs")).build()

    @Test
    fun `a single-attempt budget returns the server error without sleeping`() {
        server.enqueue(MockResponse().setResponseCode(500).setBody("boom"))
        val client = OkHttpClient.Builder()
            .addInterceptor(RetryInterceptor(maxRetries = 1, baseDelayMs = 250) { delays += it })
            .build()

        val response = client.newCall(request()).execute()

        assertEquals(500, response.code)
        assertEquals("boom", response.body?.string())
        assertEquals(1, server.requestCount)
        assertTrue("one attempt means no backoff", delays.isEmpty())
    }

    @Test
    fun `a budget of zero attempts is rejected up front`() {
        // With 0 the loop never ran and the method fell through to the
        // invented IOException the class exists to stop producing.
        assertThrows(IllegalArgumentException::class.java) {
            RetryInterceptor(maxRetries = 0)
        }
    }

    @Test
    fun `a cancelled call stops retrying instead of sleeping on a dispatcher thread`() {
        // A cover that scrolled away, or a screen the user left: Coil cancels
        // the call. Backing off on its behalf would park a dispatcher thread
        // for a response nobody wants.
        repeat(3) { server.enqueue(MockResponse().setResponseCode(500)) }
        val cancelAfterResponse = Interceptor { chain ->
            val response = chain.proceed(chain.request())
            chain.call().cancel()
            response
        }
        val client = OkHttpClient.Builder()
            .addInterceptor(RetryInterceptor(maxRetries = 3, baseDelayMs = 250) { delays += it })
            .addInterceptor(cancelAfterResponse)
            .build()

        var thrown: IOException? = null
        try {
            client.newCall(request()).execute().close()
        } catch (e: IOException) {
            thrown = e
        }

        assertNotNull("a cancelled call surfaces as an IOException, as OkHttp's own does", thrown)
        assertTrue("no backoff may run for a cancelled call, was $delays", delays.isEmpty())
    }
}
