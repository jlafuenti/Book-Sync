package com.booksync.player

/**
 * Whether the stop that is happening right now may claim the format (issue #226).
 *
 * `AudioPlayerService.onIsPlayingChanged(false)` is the single owner of the
 * pause position write — it covers the phone screen, Android Auto, the
 * notification and Cast alike, exactly like the heartbeat ([HeartbeatThrottle])
 * and the seek flush. But the listener fires for *any* reason playback stopped:
 * a deliberate pause, audio-focus loss, a Bluetooth disconnect, the sleep timer.
 * It could not tell them apart, so it passed `claimFormat = false` for all of
 * them — and the player screen ran a second, near-simultaneous write of its own
 * purely to claim the format (two `PUT /api/sync/position/...` per pause, three
 * on "switch to reader").
 *
 * The screen now sends `CMD_USER_PAUSE` before `pause()` instead, and this
 * one-shot flag carries that intent across to the listener. Deliberate stop →
 * `claimFormat = true`, one write. Involuntary stop → `false`, the same
 * conservative treatment as before (contract § "Who may claim `source`": a
 * background save must not hijack which format opens next).
 *
 * Not thread-safe: owned by [AudioPlayerService], touched only from its scope.
 */
class PauseSavePolicy {

    /** Set by the command, cleared by the next stop — or by playback resuming. */
    private var userPauseArmed = false

    /** The user asked for this pause (`CMD_USER_PAUSE` from the player screen). */
    fun onUserPauseCommand() {
        userPauseArmed = true
    }

    /**
     * The verdict for the stop happening now, consuming the arm.
     *
     * One-shot on purpose: a single tap must not license every later stop, or
     * a sleep timer firing an hour afterwards would claim the format.
     */
    fun consumeClaimFormat(): Boolean {
        val claim = userPauseArmed
        userPauseArmed = false
        return claim
    }

    /**
     * Playback (re)started, so drop any arm that never produced a stop.
     *
     * A pause command sent while the player was already paused fires no
     * `onIsPlayingChanged`, so nothing consumes the flag; without this it would
     * leak forward onto an involuntary stop.
     */
    fun onPlaybackStarted() {
        userPauseArmed = false
    }
}
