package com.booksync.data.remote

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit

/**
 * The first-run probe against a real Retrofit stack (issue #175).
 *
 * `LoginViewModelConnectionTest` mocks [BookSyncApi], so it cannot catch the two
 * ways this wiring breaks only on a device:
 *
 *  - `@Headers("...")` is parsed by Retrofit when it builds the service method,
 *    not at compile time. A malformed value throws `IllegalArgumentException` the
 *    first time anyone taps "Check connection" — on the one screen a brand-new
 *    install can reach.
 *  - `@GET` with `@Url` has to actually leave the base URL behind, and
 *    [BaseUrlInterceptor] has to let it. Getting that wrong sends the probe to
 *    whatever is already configured and reports success for an address that was
 *    never contacted.
 *
 * Two servers: "configured" and "the one the user typed". The probe must reach
 * the second while the first sits untouched.
 */
class HealthProbeWiringTest {

    private lateinit var configuredServer: MockWebServer
    private lateinit var typedServer: MockWebServer
    private lateinit var api: BookSyncApi

    @Before
    fun setUp() {
        configuredServer = MockWebServer().also { it.start() }
        typedServer = MockWebServer().also { it.start() }

        val urls = mockk<ServerUrlManager>()
        every { urls.currentUrl } returns
            configuredServer.url("/").toString().removeSuffix("/")

        val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
        api = Retrofit.Builder()
            .baseUrl(UNCONFIGURED_BASE_URL)
            .client(OkHttpClient.Builder().addInterceptor(BaseUrlInterceptor(urls)).build())
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(BookSyncApi::class.java)
    }

    @After
    fun tearDown() {
        configuredServer.shutdown()
        typedServer.shutdown()
    }

    @Test
    fun `the probe reaches the typed server and parses its answer`() {
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"status":"healthy"}"""),
        )

        val health = runBlocking {
            api.getHealth(typedServer.url("/api/health").toString())
        }

        assertEquals("healthy", health.status)
        assertEquals("/api/health", typedServer.takeRequest().path)
        assertEquals(0, configuredServer.requestCount)
    }

    @Test
    fun `the bypass marker never reaches the server`() {
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"status":"healthy"}"""),
        )

        runBlocking { api.getHealth(typedServer.url("/api/health").toString()) }

        assertNull(typedServer.takeRequest().getHeader(BYPASS_BASE_URL_HEADER))
    }

    @Test
    fun `the version handshake fields are read off a current server`() {
        // Issue #174: this is the whole wire contract between the two versions.
        // The field names are the server's Python dict keys, so a rename on
        // either side has to fail here rather than silently reading as null —
        // which is the one value that means "say nothing to the user".
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"status":"healthy","app_version":"0.1.0","api_version":1}"""),
        )

        val health = runBlocking {
            api.getHealth(typedServer.url("/api/health").toString())
        }

        assertEquals(1, health.api_version)
        assertEquals("0.1.0", health.app_version)
    }

    @Test
    fun `a server too old to send the version fields decodes to nulls`() {
        // What every deployment currently answers. A missing field must decode,
        // not throw: a server that predates the handshake is still a server the
        // user can sign in to, and `null` is what [VersionCompat] reads as
        // "unknown, warn nobody".
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"status":"healthy"}"""),
        )

        val health = runBlocking {
            api.getHealth(typedServer.url("/api/health").toString())
        }

        assertEquals("healthy", health.status)
        assertNull(health.api_version)
        assertNull(health.app_version)
        assertEquals(
            VersionCompat.Verdict.Unknown,
            VersionCompat.compare(health.api_version),
        )
    }

    @Test
    fun `a server that sends only unknown fields still parses`() {
        // Every field on HealthResponse is optional so that a server older or
        // newer than this build reads as reachable rather than broken.
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"something_else":true}"""),
        )

        val health = runBlocking {
            api.getHealth(typedServer.url("/api/health").toString())
        }

        assertNull(health.status)
    }

    // -- The demo sign-in travels the same way (issue #147) -----------------
    //
    // `LoginViewModelDemoTest` mocks BookSyncApi, so it cannot see either of the
    // things that only break against a real Retrofit: @Headers is parsed when the
    // service method is built, and @POST with @Url has to actually leave the base
    // URL behind. Both would first fail on the one screen a fresh install can
    // reach, on the tap that exists because the user has no server of their own.

    @Test
    fun `the demo sign-in posts to the demo server, not the configured one`() {
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"access_token":"a","refresh_token":"r","token_type":"bearer"}"""),
        )

        val tokens = runBlocking {
            api.loginAt(
                typedServer.url("/api/auth/login").toString(),
                LoginRequest("playreview", "demo-password"),
            )
        }

        assertEquals("a", tokens.access_token)
        val request = typedServer.takeRequest()
        assertEquals("/api/auth/login", request.path)
        assertEquals("POST", request.method)
        // The address is not stored until this answers, so the configured server
        // is still whatever it was — and must not have been asked anything.
        assertEquals(0, configuredServer.requestCount)
        assertNull(request.getHeader(BYPASS_BASE_URL_HEADER))
    }

    @Test
    fun `the demo credentials are what actually go on the wire`() {
        // A field renamed on either side would otherwise surface as a 422 on the
        // demo button and nowhere else.
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"access_token":"a","refresh_token":"r","token_type":"bearer"}"""),
        )

        runBlocking {
            api.loginAt(
                typedServer.url("/api/auth/login").toString(),
                LoginRequest("playreview", "demo-password"),
            )
        }

        val body = typedServer.takeRequest().body.readUtf8()
        assertTrue(body, body.contains(""""username":"playreview""""))
        assertTrue(body, body.contains(""""password":"demo-password""""))
    }

    @Test
    fun `the role lookup follows the demo address too`() {
        // Called after the tokens are saved but before the URL is stored, so
        // routing it through the interceptor would send it to nothing at all.
        typedServer.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"id":7,"username":"playreview","email":"d@example.com",""" +
                        """"role":"user","is_admin":false,"is_active":true,""" +
                        """"created_at":"2026-01-01T00:00:00Z"}""",
                ),
        )

        val me = runBlocking { api.getMeAt(typedServer.url("/api/auth/me").toString()) }

        assertEquals("user", me.role)
        assertEquals("/api/auth/me", typedServer.takeRequest().path)
        assertEquals(0, configuredServer.requestCount)
    }
}
