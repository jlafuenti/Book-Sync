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
 * What `POST /api/auth/register` actually returns (issue #221).
 *
 * The Retrofit declaration predates the Android register flow and claimed
 * `UserResponse`. The server never sent one: the route has no `response_model`
 * and returns a bare `{"message": ...}` (`server/routers/auth.py`). So the first
 * real "Request access" on a device created the account — 201 Created — and then
 * showed a red banner reading "Fields [id, username, email, is_admin, is_active,
 * created_at] are required for type with serial name '...UserResponse', but they
 * were missing at path: $", leaving the user on the form.
 *
 * A success that reads as a failure is worse than a failure: the obvious next
 * move is to submit again, which answers "Username already taken".
 *
 * Mocking [BookSyncApi] cannot catch this — a mock returns whatever the
 * declaration claims. Only a real Retrofit stack decoding a real server body
 * can, which is what this does.
 */
class RegisterWiringTest {

    /**
     * Copied verbatim from `server/routers/auth.py`. If the server's wording
     * changes this test still passes — it is the *shape* that is under test —
     * but keeping the real sentence here makes the contract legible.
     */
    private val serverBody =
        """{"message":"Access request submitted. An admin must approve your account before you can sign in."}"""

    private lateinit var server: MockWebServer
    private lateinit var api: BookSyncApi

    @Before
    fun setUp() {
        server = MockWebServer().also { it.start() }

        val urls = mockk<ServerUrlManager>()
        every { urls.currentUrl } returns server.url("/").toString().removeSuffix("/")

        // The same Json the app builds in AppModule; a laxer one here would
        // prove nothing about what happens on a device.
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
        server.shutdown()
    }

    private fun enqueue(body: String) {
        server.enqueue(
            MockResponse().setResponseCode(201)
                .setHeader("Content-Type", "application/json")
                .setBody(body),
        )
    }

    private fun register() = runBlocking {
        api.register(RegisterRequest("newcomer", "newcomer@example.com", "hunter2"))
    }

    @Test
    fun `the body the server actually sends decodes`() {
        enqueue(serverBody)

        val response = register()

        assertEquals(
            "Access request submitted. An admin must approve your account before you can sign in.",
            response.message,
        )
        assertEquals("/api/auth/register", server.takeRequest().path)
    }

    @Test
    fun `an empty object decodes rather than throwing`() {
        // The guard against this recurring. Every field is optional, so a server
        // that drops or renames one still reads as "created" — the account
        // exists either way, and the screen has to say so.
        enqueue("{}")

        assertNull(register().message)
    }

    @Test
    fun `unknown fields are ignored`() {
        // A newer server adding `user_id`, or an older one that returned a whole
        // user, must not turn a 201 into an error banner.
        enqueue("""{"message":"ok","user_id":7,"is_active":false}""")

        assertEquals("ok", register().message)
    }
}
