package com.booksync.data.remote

import dagger.Lazy
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #209: `must_reset_password` was set by admin-create, admin-reset and the
 * fresh-install bootstrap, and honoured by nothing but a React early-return.
 * Android had no concept of the flag at all, so the temporary password an admin
 * typed was a fully working credential here — and `AuthInterceptor` returned any
 * non-401 response untouched, so the server's new 403 would have surfaced as an
 * unexplained failure on every screen rather than a route to the reset.
 */
class PasswordResetGateTest {

    private val json = Json { ignoreUnknownKeys = true }

    // ---- the DTO ---------------------------------------------------------

    @Test
    fun `a server that does not send the flag is read as not-required`() {
        // The field is defaulted rather than required so the app keeps working
        // against a server older than this change.
        val body = """
            {"id":1,"username":"a","email":"a@b.c","is_admin":false,
             "is_active":true,"created_at":"2026-01-01T00:00:00Z"}
        """.trimIndent()

        val user = json.decodeFromString<UserResponse>(body)

        assertFalse(user.must_reset_password)
    }

    @Test
    fun `the flag is read when the server sends it`() {
        val body = """
            {"id":1,"username":"a","email":"a@b.c","is_admin":false,
             "is_active":true,"created_at":"2026-01-01T00:00:00Z",
             "must_reset_password":true}
        """.trimIndent()

        val user = json.decodeFromString<UserResponse>(body)

        assertTrue(user.must_reset_password)
    }

    // ---- the gate --------------------------------------------------------

    @Test
    fun `the gate starts down and can be raised and cleared`() {
        // Deliberately in-memory rather than persisted: it is re-derived from the
        // server on every launch, and a stale persisted `true` would lock the app
        // onto a reset screen the server no longer requires.
        val gate = PasswordResetGate()
        assertFalse(gate.required.value)

        gate.raise()
        assertTrue(gate.required.value)

        gate.clear()
        assertFalse(gate.required.value)
    }

    // ---- the interceptor -------------------------------------------------

    private fun interceptorWith(response: Response): Pair<AuthInterceptor, PasswordResetGate> {
        val tokenManager = mockk<TokenManager>(relaxed = true)
        every { tokenManager.getAccessToken() } returns flowOf("access-token")
        every { tokenManager.getRefreshToken() } returns flowOf("refresh-token")
        val api = mockk<Lazy<BookSyncApi>>(relaxed = true)
        val gate = PasswordResetGate()
        return AuthInterceptor(tokenManager, api, gate) to gate
    }

    private fun responseOf(code: Int, body: String, contentType: String = "application/json") =
        Response.Builder()
            .request(Request.Builder().url("https://tandem.example.com/api/library").build())
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message("msg")
            .body(body.toResponseBody(contentType.toMediaType()))
            .build()

    private fun chainFor(response: Response): Interceptor.Chain {
        val chain = mockk<Interceptor.Chain>()
        every { chain.request() } returns response.request
        every { chain.proceed(any()) } returns response
        return chain
    }

    @Test
    fun `a 403 naming the reset raises the gate`() {
        val response = responseOf(403, """{"detail":"password_reset_required"}""")
        val (interceptor, gate) = interceptorWith(response)

        interceptor.intercept(chainFor(response))

        assertTrue(gate.required.value)
    }

    @Test
    fun `a 403 about anything else leaves the gate alone`() {
        // require_role failures are 403 too. Bouncing the user to a
        // change-password screen because they lack the editor role would be
        // both wrong and impossible to escape.
        val response = responseOf(403, """{"detail":"Requires editor role or higher"}""")
        val (interceptor, gate) = interceptorWith(response)

        interceptor.intercept(chainFor(response))

        assertFalse(gate.required.value)
    }

    @Test
    fun `a successful response leaves the gate alone`() {
        val response = responseOf(200, """{"ok":true}""")
        val (interceptor, gate) = interceptorWith(response)

        interceptor.intercept(chainFor(response))

        assertFalse(gate.required.value)
    }

    @Test
    fun `the 403 body survives being sniffed`() {
        // Read with peekBody, not body(): consuming it here would leave callers
        // with an empty stream and no error message to show.
        val response = responseOf(403, """{"detail":"password_reset_required"}""")
        val (interceptor, _) = interceptorWith(response)

        val returned = interceptor.intercept(chainFor(response))

        assertEquals("""{"detail":"password_reset_required"}""", returned.body?.string())
    }

    @Test
    fun `a non-JSON 403 from a proxy does not crash the interceptor`() {
        val response = responseOf(403, "<html>Forbidden</html>", "text/html")
        val (interceptor, gate) = interceptorWith(response)

        interceptor.intercept(chainFor(response))

        assertFalse(gate.required.value)
    }
}
