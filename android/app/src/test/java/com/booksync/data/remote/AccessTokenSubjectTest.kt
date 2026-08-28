package com.booksync.data.remote

import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Issue #314: the Room cache was not scoped to anyone, so signing in as a second
 * account on the same device showed the first account's Continue Reading, and the
 * `pending_sync` queue would have replayed one user's unsent writes into the
 * other's account.
 *
 * Partitioning the cache needs an answer to "who is signed in", available offline
 * and at the instant tokens are stored. The access token already carries it: the
 * server mints `{"sub": str(user.id), "type": "access", "ver": …}`.
 *
 * Note `sub` is a *string* server-side, not a number — see `create_access_token`
 * in `server/routers/auth.py`.
 *
 * The signature is deliberately not verified. This is a cache-partition key, not
 * an authorization decision: the server enforces every real check, and the only
 * token anyone can tamper with is their own, so the worst outcome is partitioning
 * your own cache oddly.
 */
class AccessTokenSubjectTest {

    /** Build a JWT-shaped string with the given payload. Signature is ignored. */
    private fun tokenWith(payloadJson: String): String {
        val b64 = { s: String ->
            Base64.getUrlEncoder().withoutPadding().encodeToString(s.toByteArray())
        }
        return "${b64("""{"alg":"HS256","typ":"JWT"}""")}.${b64(payloadJson)}.fake-signature"
    }

    @Test
    fun `the subject is read from a real-shaped access token`() {
        val token = tokenWith("""{"sub":"42","type":"access","ver":3}""")
        assertEquals(42, userIdFromAccessToken(token))
    }

    @Test
    fun `padding is not required`() {
        // JWT uses base64url with the padding stripped; a decoder that insists on
        // "=" would reject most real tokens depending on payload length. Cover a
        // range of lengths so this cannot pass by luck.
        for (id in listOf("1", "12", "123", "1234", "12345")) {
            val token = tokenWith("""{"sub":"$id","type":"access","ver":0}""")
            assertEquals(id.toInt(), userIdFromAccessToken(token))
        }
    }

    @Test
    fun `a refresh token subject reads the same`() {
        // Same claim shape; nothing here should depend on `type`.
        val token = tokenWith("""{"sub":"7","type":"refresh","ver":1}""")
        assertEquals(7, userIdFromAccessToken(token))
    }

    @Test
    fun `unusable input yields null rather than throwing`() {
        // This runs on the login path and at app start. A crash here would be a
        // launch crash, so every malformed shape has to degrade quietly.
        assertNull(userIdFromAccessToken(null))
        assertNull(userIdFromAccessToken(""))
        assertNull(userIdFromAccessToken("   "))
        assertNull(userIdFromAccessToken("not-a-jwt"))
        assertNull(userIdFromAccessToken("only.two"))
        assertNull(userIdFromAccessToken("a.b.c.d"))
        assertNull(userIdFromAccessToken("header.!!!not-base64!!!.sig"))
        assertNull(userIdFromAccessToken(tokenWith("not json at all")))
        assertNull(userIdFromAccessToken(tokenWith("""{"type":"access"}""")))       // no sub
        assertNull(userIdFromAccessToken(tokenWith("""{"sub":"abc"}""")))           // non-numeric
        assertNull(userIdFromAccessToken(tokenWith("""{"sub":""}""")))
    }

    @Test
    fun `a numeric sub is accepted too`() {
        // The server sends a string today. Accepting a bare number as well costs
        // nothing and means a future server change cannot silently unscope the
        // cache — the failure mode would be everyone sharing one partition again.
        assertEquals(42, userIdFromAccessToken(tokenWith("""{"sub":42}""")))
    }
}
