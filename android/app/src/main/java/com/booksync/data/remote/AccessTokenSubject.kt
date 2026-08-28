package com.booksync.data.remote

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.util.Base64

private val lenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/**
 * The user id a stored token belongs to, or null if it cannot be determined.
 *
 * Issue #314 needs a partition key for the local cache: which account does this
 * Room data belong to? The token already answers it — the server mints
 * `{"sub": str(user.id), "type": "access", "ver": N}` (`create_access_token` in
 * `server/routers/auth.py`) — and reading it locally needs no network call, so it
 * works offline and is available the instant tokens are saved.
 *
 * **The signature is deliberately not verified.** This is a cache-partition key,
 * not an authorization decision. The server enforces every real check on every
 * request; the only token anyone can tamper with is their own, so the worst
 * outcome is someone partitioning their own cache oddly. Verifying here would
 * mean shipping the signing secret to the client, which is strictly worse.
 *
 * `java.util.Base64` rather than `android.util.Base64` for two reasons: it is
 * available from API 26 (this app's `minSdk`), and the Android one returns null
 * under `unitTests.isReturnDefaultValues`, so tests would pass against a decoder
 * that never ran.
 *
 * Returns null rather than throwing for every malformed shape — this runs on the
 * login path and at app start, where an exception is a launch crash.
 */
fun userIdFromAccessToken(token: String?): Int? {
    val parts = token?.trim()?.split('.') ?: return null
    if (parts.size != 3) return null

    val payload = try {
        // JWT is base64url with the padding stripped; Java's decoder wants it back.
        val segment = parts[1]
        val padded = segment + "=".repeat((4 - segment.length % 4) % 4)
        String(Base64.getUrlDecoder().decode(padded))
    } catch (e: IllegalArgumentException) {
        return null
    }

    return try {
        val sub = lenientJson.parseToJsonElement(payload).jsonObject["sub"] ?: return null
        sub.jsonPrimitive.content.toIntOrNull()
    } catch (e: Exception) {
        // Any parse failure means "unknown user", which callers already handle.
        null
    }
}
