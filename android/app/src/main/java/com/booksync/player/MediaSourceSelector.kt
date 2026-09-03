package com.booksync.player

import com.booksync.data.remote.normalizeServerUrl
import java.io.File
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where the audio for one book actually comes from.
 *
 * Deliberately not a `Uri` — that is an Android type, and this decision has to
 * stay JVM-testable (see `AudioSource.toUri()` in `AudioSourceUri.kt` for the
 * one-line bridge every call site uses).
 */
sealed interface AudioSource {
    /** The downloaded copy on this device. Offline, and the only thing Cast can serve. */
    data class LocalFile(val file: File) : AudioSource

    /** `{serverUrl}/api/files/audiobook/{id}`, played through the authenticated OkHttp client. */
    data class Stream(val url: String) : AudioSource
}

/**
 * "The downloaded file if it is there, otherwise stream it from the server."
 *
 * Issue #171: every `MediaItem` in the app used to be built from
 * `Uri.fromFile(...)`, so a book that had not been fully downloaded — often
 * several hundred MB — simply would not play on the phone while the browser
 * streamed it instantly. `GET /api/files/audiobook/{id}` accepts the same
 * Bearer token as every other call and honours HTTP Range, so the phone can do
 * what the web does; download stays as the offline option.
 *
 * Two things this must not do:
 *
 *  - **Put a credential in the URL.** The endpoint also takes a `?token=` media
 *    token, which exists for consumers that cannot send a header (an `<img>`
 *    tag, the Cast receiver). Android can send the header, and a URL ends up in
 *    logcat — so the URL built here is bare. Auth arrives via `AuthInterceptor`
 *    on the OkHttp client backing ExoPlayer's data source, which also means
 *    `TokenAuthenticator` refreshes a 401 mid-stream for free.
 *  - **Depend on a Room row.** `localAudioFile` returns null for a filename the
 *    app refuses to resolve (issue #177), and a search result can name an id
 *    Room has never seen (issue #338). Neither is a reason to have nothing to
 *    play: the id and the server URL are enough.
 */
object MediaSourceSelector {

    /** Owned here so no call site hand-rolls it; mirrors the web's `getAudiobookStreamUrl`. */
    private const val AUDIOBOOK_PATH = "api/files/audiobook"

    /**
     * The bare stream URL, or null when there is nothing sane to build.
     *
     * The server URL is normalised by [normalizeServerUrl] — the same function
     * behind `retrofitBaseUrl`, so trailing slashes, a bare host, a sub-path
     * deployment and a non-default port are all treated identically to every
     * other request the app makes. Unlike `retrofitBaseUrl` this returns null
     * rather than falling back to `UNCONFIGURED_BASE_URL`: that placeholder
     * exists so Retrofit can be constructed at all, and a player chasing
     * `http://localhost:8000` would report a connection error instead of the
     * truth, which is that no server is configured.
     */
    fun audiobookStreamUrl(serverUrl: String, audiobookId: Int): String? {
        if (audiobookId <= 0) return null
        val base = normalizeServerUrl(serverUrl) ?: return null
        val parsed = "$base/".toHttpUrlOrNull() ?: return null
        return parsed.newBuilder()
            .addPathSegments(AUDIOBOOK_PATH)
            .addPathSegment(audiobookId.toString())
            .build()
            .toString()
    }

    /**
     * @param localFile where a download would have landed, or null if the app
     *   would not resolve one. A directory or a zero-length file counts as
     *   *not* downloaded — a half-written download leaves one behind, and
     *   playing it is an instant error where streaming is not.
     */
    fun select(localFile: File?, serverUrl: String, audiobookId: Int): AudioSource? {
        if (localFile != null && localFile.isFile && localFile.length() > 0L) {
            return AudioSource.LocalFile(localFile)
        }
        return audiobookStreamUrl(serverUrl, audiobookId)?.let(AudioSource::Stream)
    }
}
