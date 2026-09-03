package com.booksync.data.remote

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import retrofit2.HttpException

/**
 * FastAPI's error shape, `{"detail": "..."}`.
 *
 * Worth reading rather than showing the status code because the server's own
 * sentence is usually the only thing that tells the user what to do next:
 * "Public registration is disabled" means stop retrying and ask an admin,
 * "Username already registered" means pick another name. `HttpException.message`
 * is "HTTP 403 Forbidden", which says neither (issue #221).
 */
private val lenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/**
 * The server's `detail` string, or null when there isn't one to show.
 *
 * Null rather than a guess for every shape that isn't a plain string: FastAPI's
 * 422 puts a *list* of validation objects in `detail`, and anything in front of
 * the server (nginx, a captive portal) answers with HTML. Rendering either raw
 * would be worse than the generic message the caller falls back to.
 *
 * The error body is a one-shot stream, so this consumes it — call it once.
 */
fun HttpException.serverDetail(): String? {
    val body = runCatching { response()?.errorBody()?.string() }.getOrNull()
    if (body.isNullOrBlank()) return null
    val detail = runCatching {
        (lenientJson.parseToJsonElement(body) as? JsonObject)?.get("detail")
    }.getOrNull()
    return (detail as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
}
