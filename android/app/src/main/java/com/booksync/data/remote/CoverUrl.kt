package com.booksync.data.remote

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Absolute URL for a server-hosted cover, built from the configured server URL
 * and the stored `cover_path` (e.g. "/api/files/covers/Private_#1_Suspect.jpg").
 *
 * Every path segment is percent-encoded. Cover filenames are derived from the
 * book's author and title, so they routinely contain spaces and can contain a
 * '#'; the string concatenation this replaced handed Coil a raw URL, and OkHttp
 * read the '#' as a fragment delimiter -- the request path was truncated and the
 * cover 404'd (issue #126). The web client's `coverSrc()` does the same encoding.
 *
 * Kept free of Android and DI types so it's unit-testable, same pattern as
 * [retrofitBaseUrl] in `ServerUrlPolicy`.
 *
 * Returns null when the server URL isn't usable or the path is blank, so callers
 * fall through to their placeholder instead of handing Coil a broken model.
 */
fun coverImageUrl(serverUrl: String, coverPath: String): String? {
    val base = (serverUrl.trim().trimEnd('/') + "/").toHttpUrlOrNull() ?: return null
    val segments = coverPath.trim().trim('/').split('/').filter { it.isNotEmpty() }
    if (segments.isEmpty()) return null
    val builder = base.newBuilder()
    segments.forEach { builder.addPathSegment(it) }
    return builder.build().toString()
}
