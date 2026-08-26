package com.booksync.data.repository

/**
 * Timeouts shared by every surface that pulls the server position before
 * resuming (contract § "The write gate": "Resume paths refresh first …
 * bounded, then fall back to cache").
 *
 * One symbol, used by the phone player, AudioPlayerService (Android Auto and
 * Cast resumption) and the reader — a private copy per file is how the reader
 * ended up with no bound at all (issue #167).
 */
object PositionSyncTimeouts {
    /**
     * How long a resume path waits for the server's position (and sync map)
     * before falling back to the local cache. Long enough for a normal
     * request, short enough that an unreachable server doesn't visibly delay
     * playback or the reader opening.
     */
    const val SERVER_POSITION_TIMEOUT_MS = 1500L
}
