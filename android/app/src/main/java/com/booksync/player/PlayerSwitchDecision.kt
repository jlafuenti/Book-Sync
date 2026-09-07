package com.booksync.player

import androidx.media3.common.Player
import com.google.android.gms.cast.MediaStatus

/** Where the player being switched *to* starts, and whether it starts playing. */
data class PlayerSwitchDecision(val positionMs: Long, val shouldPlay: Boolean)

/**
 * The Cast handoff's two decisions (issue #225), taken from the player being
 * switched *away from*.
 *
 * When a Cast session ends, `CastPlayer.currentPosition` often reads 0 because
 * it has already disconnected from the receiver, so [lastKnownCastPositionMs] —
 * the last position the receiver reported — stands in for it; local playback
 * then resumes where casting left off instead of at 0:00. Going *to* Cast never
 * substitutes: a stale receiver position from a previous session must not
 * override where the phone actually is.
 *
 * Playback carries over only if it was on and the book had not ended: a
 * finished book handed to a new player would otherwise restart itself.
 */
fun decidePlayerSwitch(
    leavingCast: Boolean,
    rawPositionMs: Long,
    lastKnownCastPositionMs: Long,
    playWhenReady: Boolean,
    playbackState: Int,
): PlayerSwitchDecision {
    val positionMs =
        if (leavingCast && rawPositionMs <= 0L && lastKnownCastPositionMs > 0L) lastKnownCastPositionMs
        else rawPositionMs
    return PlayerSwitchDecision(
        positionMs = positionMs,
        shouldPlay = playWhenReady && playbackState != Player.STATE_ENDED,
    )
}

/**
 * The receiver's status updates carry a stream position; only a positive one
 * is worth remembering, because a 0 arrives on every idle/loading transition
 * and would wipe the value the switch-back depends on.
 */
fun castReportedPosition(previousMs: Long, reportedMs: Long): Long =
    if (reportedMs > 0L) reportedMs else previousMs

/** A readable `MediaStatus` for the log line, falling back to the raw codes. */
fun castStatusLabel(playerState: Int, idleReason: Int): String {
    val stateName = when (playerState) {
        MediaStatus.PLAYER_STATE_UNKNOWN -> "UNKNOWN"
        MediaStatus.PLAYER_STATE_IDLE -> "IDLE"
        MediaStatus.PLAYER_STATE_PLAYING -> "PLAYING"
        MediaStatus.PLAYER_STATE_PAUSED -> "PAUSED"
        MediaStatus.PLAYER_STATE_BUFFERING -> "BUFFERING"
        MediaStatus.PLAYER_STATE_LOADING -> "LOADING"
        else -> "state=$playerState"
    }
    val idleReasonName = when (idleReason) {
        MediaStatus.IDLE_REASON_NONE -> "none"
        MediaStatus.IDLE_REASON_FINISHED -> "FINISHED"
        MediaStatus.IDLE_REASON_CANCELED -> "CANCELED"
        MediaStatus.IDLE_REASON_INTERRUPTED -> "INTERRUPTED"
        MediaStatus.IDLE_REASON_ERROR -> "ERROR"
        else -> "reason=$idleReason"
    }
    return "$stateName idle=$idleReasonName"
}
