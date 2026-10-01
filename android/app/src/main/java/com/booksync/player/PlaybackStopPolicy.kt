package com.booksync.player

import androidx.media3.common.Player

/**
 * Whether playback has just *stopped*, as opposed to stalled (issue #766).
 *
 * `AudioPlayerService` runs its pause write (stop the heartbeat, push the
 * position, append a history entry) on a stop. It used to treat every
 * `onIsPlayingChanged(false)` as one, but Media3 reports `isPlaying = false`
 * whenever the state leaves READY — every rebuffer. A stream stalling on a weak
 * connection flapped READY/BUFFERING about once a second and pushed a position
 * on each flap, racing itself into 409s.
 *
 * A stall is BUFFERING while the user still wants playback (`playWhenReady`):
 * the heartbeat keeps running (it pushes at most every 30 s through
 * [HeartbeatThrottle]) and nothing extra is written. Everything else that leaves
 * the player silent is a stop — a pause, including one pressed *during* a stall,
 * audio-focus loss, an error, the end of the book.
 *
 * `heartbeatRunning` is the "were we playing" edge: the heartbeat starts on
 * `isPlaying = true` and the stop write tears it down, so the initial load
 * (BUFFERING before the first sound) is not a stop, and the several callbacks
 * one real stop produces make exactly one write.
 */
object PlaybackStopPolicy {

    fun isStop(
        heartbeatRunning: Boolean,
        isPlaying: Boolean,
        playbackState: Int,
        playWhenReady: Boolean,
    ): Boolean {
        if (!heartbeatRunning || isPlaying) return false
        val stalled = playbackState == Player.STATE_BUFFERING && playWhenReady
        return !stalled
    }
}
