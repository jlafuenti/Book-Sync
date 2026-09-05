package com.booksync.player

/**
 * One place cover art can come from. See [coverArtPlan].
 */
sealed class CoverArtRung {
    /** Already on disk at `filesDir/covers/{audiobookId}.jpg`. */
    data object Cached : CoverArtRung()

    /** Embedded in the downloaded audio file — an M4B `covr` atom or an MP3 `APIC` frame. */
    data object Embedded : CoverArtRung()

    /** The server's cover, at this API path; fetched and then cached. */
    data class Server(val coverPath: String) : CoverArtRung()
}

/**
 * The ordered list of places to look for an audiobook's cover art (issue #331).
 *
 * The app used to look in exactly two: the cache, and art embedded in the audio
 * file. A book whose file carried neither a `covr` atom nor an `APIC` frame got
 * a headphones placeholder in the player, the notification, the lock screen and
 * Android Auto — while every other screen, and the web UI, happily showed the
 * cover the server had for that same book. The server rung is the fix.
 *
 * Returns a *plan* rather than a single source because "does this file have
 * embedded art" cannot be answered without doing the extraction: the caller
 * walks the rungs and stops at the first that yields bytes. Same shape as the
 * reader's restore ladder.
 *
 * Pure, and deliberately not in `CoverArtHelper` — that class is excluded from
 * Kover as untestable framework glue (`app/build.gradle.kts`), so logic placed
 * there cannot be covered. This mirrors [HeartbeatThrottle] and
 * `PositionSavePolicy`. (The wider `com.booksync.auto` package stopped being
 * excluded in issue #172, when the browse tree and search matcher moved into
 * it as pure code.)
 *
 * @param cachedExists    `filesDir/covers/{audiobookId}.jpg` is present
 * @param audioFileExists the audiobook is downloaded, so it can be scanned
 * @param serverCoverPath the API path from `audiobooks.cover_path`, or null
 */
fun coverArtPlan(
    cachedExists: Boolean,
    audioFileExists: Boolean,
    serverCoverPath: String?,
): List<CoverArtRung> {
    // A media scan of a ~1 GB M4B and a network round trip are both far more
    // expensive than a stat, so a hit here ends it.
    if (cachedExists) return listOf(CoverArtRung.Cached)

    return buildList {
        if (audioFileExists) add(CoverArtRung.Embedded)
        // Blank is not a path: the server sends null for "no cover", but an
        // empty or whitespace string would otherwise build a URL to the API
        // root and 404 on every open.
        serverCoverPath?.takeIf { it.isNotBlank() }?.let { add(CoverArtRung.Server(it)) }
    }
}
