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
}
