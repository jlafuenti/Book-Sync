package com.booksync.data.remote

import io.mockk.every
import io.mockk.mockk
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Changing the server must not require killing the process (issue #228).
 *
 * Retrofit binds `baseUrl` once, at singleton construction, so switching servers
 * used to mean `Runtime.getRuntime().exit(0)` — an actual process kill. That is
 * worse than the UX suggests: `docs/position-sync-contract.md` requires the final
 * position flush to run in an application-scoped, non-cancellable coroutine, and
 * `exit(0)` does not wait for it. The restart could take a reading position with
 * it, which is the one thing this app exists to protect.
 *
 * So the base URL moves out of Retrofit and into an interceptor that reads
 * [ServerUrlManager.currentUrl] per request. Retrofit keeps a fixed placeholder
 * it never actually reaches.
 *
 * Two MockWebServers, because "the switch took effect" is only observable as a
 * request arriving at a different host — the same reason TokenRefreshTest runs
 * two for its cross-host redirect case.
 */
class BaseUrlInterceptorTest {

    private lateinit var serverA: MockWebServer
    private lateinit var serverB: MockWebServer
    private lateinit var urls: ServerUrlManager

    /** Whatever the manager currently points at; the interceptor re-reads it. */
    private var configured = ""

    @Before
    fun setUp() {
        serverA = MockWebServer().also { it.start() }
        serverB = MockWebServer().also { it.start() }
        urls = mockk()
        every { urls.currentUrl } answers { configured }
    }

    @After
    fun tearDown() {
        serverA.shutdown()
        serverB.shutdown()
    }

    private fun client() = OkHttpClient.Builder()
        .addInterceptor(BaseUrlInterceptor(urls))
        .build()

    /** Always addressed to the placeholder, exactly as Retrofit would. */
    private fun call(c: OkHttpClient, path: String = "/api/library/pairs") =
        c.newCall(
            Request.Builder().url("$UNCONFIGURED_BASE_URL${path.removePrefix("/")}").build()
        ).execute()

    @Test
    fun `requests follow the configured server without a restart`() {
        serverA.enqueue(MockResponse().setResponseCode(200).setBody("a"))
        serverB.enqueue(MockResponse().setResponseCode(200).setBody("b"))
        val c = client()

        configured = serverA.url("/").toString().removeSuffix("/")
        assertEquals("a", call(c).body?.string())

        // The switch a user makes in Account -> Server URL. No new client, no
        // new process: the very next request goes somewhere else.
        configured = serverB.url("/").toString().removeSuffix("/")
        assertEquals("b", call(c).body?.string())

        assertEquals(1, serverA.requestCount)
        assertEquals(1, serverB.requestCount)
    }

    @Test
    fun `the path and query survive the rewrite`() {
        serverA.enqueue(MockResponse().setResponseCode(200))
        configured = serverA.url("/").toString().removeSuffix("/")

        call(client(), "/api/library/pairs?page=2&limit=50").close()

        assertEquals("/api/library/pairs?page=2&limit=50", serverA.takeRequest().path)
    }

    @Test
    fun `a server on a non-default port is honoured`() {
        // MockWebServer always picks an ephemeral port, so this is the normal
        // case here — but it is also the case a naive host-only rewrite breaks.
        serverB.enqueue(MockResponse().setResponseCode(200).setBody("b"))
        configured = serverB.url("/").toString().removeSuffix("/")

        assertEquals("b", call(client()).body?.string())
        assertEquals(serverB.port, serverB.takeRequest().requestUrl?.port)
    }

    @Test
    fun `an unconfigured server leaves the request alone`() {
        // Nothing to rewrite to. The request still fails, but it fails as a
        // connection error to the placeholder rather than being sent somewhere
        // unintended — and the login screen is what fixes it.
        configured = ""
        serverA.enqueue(MockResponse().setResponseCode(200))

        runCatching { call(client()).close() }

        assertEquals(0, serverA.requestCount)
    }

    @Test
    fun `a garbage stored value does not redirect the request`() {
        // ServerUrlManager.repair should make this unreachable, but the
        // interceptor is on every request in the app and must not throw if it
        // ever sees one.
        configured = "not a url"
        serverA.enqueue(MockResponse().setResponseCode(200))

        runCatching { call(client()).close() }

        assertEquals(0, serverA.requestCount)
    }
}
