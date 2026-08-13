package com.booksync.player

/**
 * When a continuous-playback history entry is due.
 *
 * The Session History panel should show a coarse trail — pause, stop, and one
 * entry per [intervalMs] of uninterrupted listening — not one row per 5-second
 * heartbeat.
 *
 * This exists as its own object because the previous inline version was a bare
 * `var lastLogTimeMs = 0L` compared against wall-clock time. `now - 0` is the
 * whole Unix epoch, so the "have 30 minutes elapsed?" test was true on the
 * *first* tick of every session and each playback start wrote a bogus
 * 30-minutes-of-listening entry. A null seed makes "never started" a state the
 * type can represent, rather than a number that happens to compare wrong.
 *
 * Not thread-safe: it is owned by [AudioPlayerService] and only touched from
 * the player listener and the heartbeat loop, both on the service's scope.
 */
class ContinuousPlaybackLog(private val intervalMs: Long) {

    /** Null until playback has actually started; see the class docstring. */
    private var lastLoggedAtMs: Long? = null

    /**
     * Playback began. Seeds the interval so the first entry lands [intervalMs]
     * from now rather than immediately.
     *
     * A resume is not a restart: pauses freeze the clock by tearing the
     * heartbeat loop down, and a pause that writes an entry calls [onLogged],
     * so the timer is already positioned correctly. Reseeding here would mean
     * a listener who pauses every few minutes never accumulates an entry.
     */
    fun onPlaybackStarted(nowMs: Long) {
        if (lastLoggedAtMs == null) lastLoggedAtMs = nowMs
    }

    /** Whether enough uninterrupted playback has passed to warrant an entry. */
    fun isDue(nowMs: Long): Boolean {
        val last = lastLoggedAtMs ?: return false
        return nowMs - last >= intervalMs
    }

    /**
     * An entry was written. Also seeds the timer when a boundary save (pause,
     * natural end) logs before any heartbeat has run.
     */
    fun onLogged(nowMs: Long) {
        lastLoggedAtMs = nowMs
    }
}
