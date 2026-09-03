package com.booksync.data.auth

import org.junit.Assert.assertEquals
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import okhttp3.ResponseBody.Companion.toResponseBody

/**
 * What a failed library action says to the user (issue #170).
 *
 * The snackbar rendered `e.message` verbatim, so tapping an editor-only action
 * as a plain user produced `"HTTP 403 "` — technically accurate, useless to
 * read, and the visible half of the bug this issue is about.
 */
class UserFacingErrorTest {

    private fun http(code: Int) =
        HttpException(Response.error<Any>(code, "".toResponseBody(null)))

    @Test
    fun `a permission failure explains itself`() {
        assertEquals(
            "You don't have permission to change the library.",
            userFacingError(http(403)),
        )
    }

    @Test
    fun `an expired session says so`() {
        assertEquals("Your session has expired. Sign in again.", userFacingError(http(401)))
    }

    @Test
    fun `other failures keep their own message`() {
        // Not everything should be flattened — a real error is more useful than
        // a generic one.
        assertEquals("Unable to resolve host", userFacingError(Exception("Unable to resolve host")))
    }

    @Test
    fun `a message-less failure still says something`() {
        assertEquals("Action failed", userFacingError(Exception()))
    }
}
