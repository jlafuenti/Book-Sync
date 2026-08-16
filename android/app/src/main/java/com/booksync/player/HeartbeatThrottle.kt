package com.booksync.player

/**
 * When a position heartbeat may push to the server (issue #65).
 *
 * The 5-second heartbeat in [AudioPlayerService] used to PUT on every tick —
 * ~720 Postgres writes an hour per device to record "position advanced 5s",
 * and a radio wake-up every 5s on the phone. The local Room row is what makes a
 * crash cheap; the server copy only matters for cross-device resume, where
 * [intervalMs] (30s) of staleness is imperceptible. So every tick still writes
 * Room, but the network push waits for the window — and session boundaries
 * (pause, stop, track end, cast switch, disconnect) push immediately and call
 * [onPushed], so a device that stops listening leaves an exact position.
 *
 * Only *successful* pushes are reported: a failed PUT leaves the window where
 * it was, so the next tick retries instead of waiting out another 30s.
 *
 * Not thread-safe: owned by [AudioPlayerService], touched only from its scope.
 */
class HeartbeatThrottle(private val intervalMs: Long) {

    /** Null until something has been pushed; the first tick of a session pushes. */
    private var lastPushedAtMs: Long? = null

    /** Whether the heartbeat firing at [nowMs] should also push to the server. */
    fun shouldPush(nowMs: Long): Boolean {
        val last = lastPushedAtMs ?: return true
        return nowMs - last >= intervalMs
    }

    /** A push reached the server. Boundary saves report here too. */
    fun onPushed(nowMs: Long) {
        lastPushedAtMs = nowMs
    }

    /** Forget the window — a new item is playing and deserves a prompt first push. */
    fun reset() {
        lastPushedAtMs = null
    }
}
