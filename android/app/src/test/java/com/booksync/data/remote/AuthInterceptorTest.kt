package com.booksync.data.remote

import io.mockk.every
import io.mockk.mockk
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * [AuthInterceptor] on its own (issue #217). The refresh-on-401 behaviour that
 * used to live here is [TokenAuthenticator]'s and is pinned by
 * [TokenRefreshTest]; this file covers the two things the interceptor still
 * does — attach the cached bearer, and notice a forced password reset — at
 * their edges.
 *
 * MockWebServer rather than a mocked chain, for the same reason as the rest of
 * the HTTP tests: what matters is the request that actually leaves.
 */
class AuthInterceptorTest {

    private lateinit var server: MockWebServer
    private val tokenManager = mockk<TokenManager>(relaxed = true)
    private val gate = PasswordResetGate()

    @Volatile
    private var stored: String? = "tok-1"

    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder().addInterceptor(AuthInterceptor(tokenManager, gate)).build()
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        stored = "tok-1"
        every { tokenManager.cachedAccessToken() } answers { stored }
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun get(path: String = "/api/library/pairs", build: Request.Builder.() -> Unit = {}) =
        client.newCall(Request.Builder().url(server.url(path)).apply(build).build()).execute()

    @Test
    fun `an empty token attaches no header`() {
        // A blank string is "no session" too; `Bearer ` with nothing after it
        // would be a malformed header the server rejects as a bad token.
        stored = ""
        server.enqueue(MockResponse().setResponseCode(200))

        get().close()

        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `a stale Authorization header on the request is replaced, not stacked`() {
        server.enqueue(MockResponse().setResponseCode(200))

        get { header("Authorization", "Bearer old") }.close()

        assertEquals(listOf("Bearer tok-1"), server.takeRequest().headers.values("Authorization"))
    }

    @Test
    fun `the token is read afresh for every request`() {
        // Nothing here may cache the bearer per client: after a refresh, the
        // very next request must carry the new one.
        server.enqueue(MockResponse().setResponseCode(200))
        server.enqueue(MockResponse().setResponseCode(200))

        get().close()
        stored = "tok-2"
        get().close()

        assertEquals("Bearer tok-1", server.takeRequest().getHeader("Authorization"))
        assertEquals("Bearer tok-2", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `a 403 with an empty body is passed through untouched`() {
        server.enqueue(MockResponse().setResponseCode(403))

        val response = get()

        assertEquals(403, response.code)
        assertEquals("", response.body?.string())
        assertFalse(gate.required.value)
    }

    @Test
    fun `the reset marker is only honoured on a 403`() {
        // A 200 whose body happens to mention the marker — a settings page, a
        // log listing — must not throw the user into the reset screen.
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"detail":"password_reset_required"}""")
        )

        get().close()

        assertFalse(gate.required.value)
    }

    @Test
    fun `the sniff reads the head of the body, which is where the server puts the detail`() {
        // peekBody(512): the server's refusal is a short `{"detail": ...}` and
        // the interceptor must not buffer an arbitrary body on every 403. A
        // marker past that bound is not seen, and that is the documented trade.
        server.enqueue(
            MockResponse().setResponseCode(403).setBody("""{"detail":"password_reset_required"}""")
        )
        server.enqueue(
            MockResponse().setResponseCode(403)
                .setBody("{\"padding\":\"" + "x".repeat(600) + "\",\"detail\":\"password_reset_required\"}")
        )

        get().close()
        assertTrue("the ordinary refusal must raise the gate", gate.required.value)

        val buried = PasswordResetGate()
        val other = OkHttpClient.Builder().addInterceptor(AuthInterceptor(tokenManager, buried)).build()
        other.newCall(Request.Builder().url(server.url("/api/x")).build()).execute().close()
        assertFalse("a marker buried past the peek is not read", buried.required.value)
    }
}
