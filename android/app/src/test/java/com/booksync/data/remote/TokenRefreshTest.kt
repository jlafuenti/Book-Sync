package com.booksync.data.remote

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Issue #143: a rejected refresh token deadlocked every network call in the process.
 *
 * `AuthInterceptor` refreshed inline through the *same* `OkHttpClient` it was
 * installed on, so the refresh's own 401 re-entered the interceptor and refreshed
 * again. Each level parked a dispatcher thread in `runBlocking`; with OkHttp's
 * default `maxRequestsPerHost = 5` the sixth nested refresh queued behind five
 * blocked threads and never ran. From then on nothing completed — including
 * `logout()`, so the user could not even sign out, and Coil shares the client so
 * covers hung too. Force-stop and clear data was the only way out, taking every
 * queued position write with it.
 *
 * A mocked `Interceptor.Chain` cannot show this: the bug is about how many
 * requests actually leave. MockWebServer counts them, so "did it recurse" and
 * "did N concurrent 401s cause N refreshes" become plain assertions.
 */
class TokenRefreshTest {

    private lateinit var server: MockWebServer
    private lateinit var tokenManager: TokenManager
    private var refreshCalls = 0

    /**
     * The stored access token, as a real value rather than a fixed stub.
     *
     * Single-flighting depends on `saveTokens` updating what `cachedAccessToken`
     * returns: that is how a request waiting on the mutex discovers the winner
     * already refreshed and retries with its token instead of refreshing again. A
     * static stub hides that coupling and the test would pass against a design
     * that refreshes N times.
     */
    @Volatile
    private var storedAccess: String? = "stale-access"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        refreshCalls = 0
        tokenManager = mockk(relaxed = true)
        storedAccess = "stale-access"
        // Non-suspend accessors, so `every` — `coEvery` silently fails to match
        // and the relaxed mock then reports no session at all.
        every { tokenManager.cachedAccessToken() } answers { storedAccess }
        every { tokenManager.currentRefreshToken() } returns "stale-refresh"
        coEvery { tokenManager.saveTokens(any(), any()) } answers { storedAccess = firstArg() }
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** The refresh endpoint, on its own client so it can never re-enter. */
    private fun refreshApi(response: () -> MockResponse): AuthRefreshApi = mockk {
        coEvery { refreshToken(any()) } answers {
            refreshCalls++
            val r = response()
            if (r.status.contains(" 200")) {
                TokenResponse(access_token = "fresh-access", refresh_token = "fresh-refresh")
            } else {
                throw retrofit2.HttpException(
                    retrofit2.Response.error<TokenResponse>(
                        401,
                        okhttp3.ResponseBody.create(null, "invalid refresh"),
                    )
                )
            }
        }
    }

    private fun client(refreshSucceeds: Boolean): OkHttpClient {
        val api = refreshApi {
            if (refreshSucceeds) MockResponse().setResponseCode(200)
            else MockResponse().setResponseCode(401)
        }
        return OkHttpClient.Builder()
            .callTimeout(10, TimeUnit.SECONDS)
            .addInterceptor(AuthInterceptor(tokenManager, mockk(relaxed = true)))
            .authenticator(TokenAuthenticator(tokenManager) { api })
            .build()
    }

    private fun call(c: OkHttpClient) =
        c.newCall(Request.Builder().url(server.url("/api/library/pairs")).build()).execute()

    @Test
    fun `a rejected refresh does not recurse`() {
        // The deadlock. Every response is 401, including what the refresh would
        // get; the client must give up rather than refresh again.
        repeat(10) { server.enqueue(MockResponse().setResponseCode(401)) }

        val response = call(client(refreshSucceeds = false))

        assertEquals(401, response.code)
        assertEquals(
            "the original request should be attempted once and not retried after a " +
                "failed refresh — anything more is the recursion that deadlocked the app",
            1, server.requestCount,
        )
        assertEquals("exactly one refresh attempt", 1, refreshCalls)
        response.close()
    }

    @Test
    fun `a successful refresh retries the original request with the new token`() {
        server.enqueue(MockResponse().setResponseCode(401))
        server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))

        val response = call(client(refreshSucceeds = true))

        assertEquals(200, response.code)
        assertEquals(2, server.requestCount)
        server.takeRequest()
        val retried = server.takeRequest()
        assertEquals("Bearer fresh-access", retried.getHeader("Authorization"))
        response.close()
    }

    @Test
    fun `a failed refresh clears the tokens exactly once`() {
        repeat(5) { server.enqueue(MockResponse().setResponseCode(401)) }

        call(client(refreshSucceeds = false)).close()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `the failed request is not re-issued unauthenticated`() {
        // The old code retried without a bearer, which produced a second 401 and
        // told the caller nothing. The 401 should simply surface.
        repeat(5) { server.enqueue(MockResponse().setResponseCode(401)) }

        val response = call(client(refreshSucceeds = false))

        assertEquals(401, response.code)
        assertEquals(1, server.requestCount)
        response.close()
    }

    @Test
    fun `concurrent 401s cause a single refresh`() {
        // Every in-flight request 401s when the access token expires. Without
        // single-flighting, each one POSTs its own refresh and they race each
        // other into DataStore.
        repeat(20) { server.enqueue(MockResponse().setResponseCode(401)) }
        repeat(20) { server.enqueue(MockResponse().setResponseCode(200).setBody("ok")) }
        val c = client(refreshSucceeds = true)

        val threads = (1..5).map { Thread { call(c).close() } }
        threads.forEach { it.start() }
        threads.forEach { it.join(10_000) }

        assertEquals("five concurrent 401s must produce one refresh", 1, refreshCalls)
    }

    @Test
    fun `a request that already carries the newest token is retried rather than refreshed again`() {
        // Losers of the single-flight race must pick up the token the winner
        // stored, not queue another refresh behind it. The request goes out with
        // the old token; by the time the 401 comes back the cache holds a newer
        // one, which is exactly the state a loser wakes up in.
        every { tokenManager.cachedAccessToken() } returnsMany
            listOf("stale-access", "someone-elses-fresh-token", "someone-elses-fresh-token")
        server.enqueue(MockResponse().setResponseCode(401))
        server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))

        val response = call(client(refreshSucceeds = true))

        assertEquals(200, response.code)
        assertEquals("no refresh was needed", 0, refreshCalls)
        response.close()
    }

    @Test
    fun `the interceptor attaches the cached token without touching DataStore`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))

        call(client(refreshSucceeds = true)).close()

        val sent = server.takeRequest()
        assertEquals("Bearer stale-access", sent.getHeader("Authorization"))
        // The hot path must not do a blocking DataStore read per request.
        io.mockk.verify(exactly = 0) { tokenManager.getAccessToken() }
    }

    @Test
    fun `no bearer header is attached when there is no session`() {
        every { tokenManager.cachedAccessToken() } returns null
        server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))

        call(client(refreshSucceeds = true)).close()

        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `the forced-password-reset gate still fires from a 403`() {
        // Moved when the refresh logic left the interceptor (issue #209); pin it
        // so the split did not quietly drop it.
        val gate = PasswordResetGate()
        val c = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenManager, gate))
            .build()
        server.enqueue(
            MockResponse().setResponseCode(403).setBody("""{"detail":"password_reset_required"}""")
        )

        val response = c.newCall(
            Request.Builder().url(server.url("/api/library/pairs")).build()
        ).execute()

        assertTrue(gate.required.value)
        assertEquals(
            "the body must survive being sniffed",
            """{"detail":"password_reset_required"}""", response.body?.string(),
        )
    }

    @Test
    fun `a 403 about roles does not raise the gate`() {
        val gate = PasswordResetGate()
        val c = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenManager, gate))
            .build()
        server.enqueue(
            MockResponse().setResponseCode(403)
                .setBody("""{"detail":"Requires editor role or higher"}""")
        )

        c.newCall(Request.Builder().url(server.url("/api/x")).build()).execute().close()

        assertTrue(!gate.required.value)
    }
}
