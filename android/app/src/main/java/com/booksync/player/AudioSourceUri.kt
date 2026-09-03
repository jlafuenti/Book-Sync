package com.booksync.player

import android.net.Uri

/**
 * The Android URI for a resolved [AudioSource].
 *
 * One line, in its own file, so that [MediaSourceSelector] stays free of
 * `android.net.Uri` — which is a stub that throws in JVM unit tests, and would
 * take the whole selector out of reach of `MediaSourceSelectorTest`.
 *
 * It is also the only remaining `Uri.fromFile` in the playback paths, which is
 * what lets `MediaSourceWiringTest` assert that no `MediaItem` builder pins
 * itself to a local file behind the selector's back (issue #171).
 */
fun AudioSource.toUri(): Uri = when (this) {
    is AudioSource.LocalFile -> Uri.fromFile(file)
    is AudioSource.Stream -> Uri.parse(url)
}
