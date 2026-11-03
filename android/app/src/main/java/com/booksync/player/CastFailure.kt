package com.booksync.player

/**
 * A Cast session that was ended because the speaker could not play from the
 * phone (issue #932). Carried from `AudioPlayerService` to the player screen by
 * `CastSessionMonitor`, which is the only thing both can reach.
 *
 * [address] is where the phone was serving when it failed (null if the server
 * was already gone), so the screen can say which port to allow.
 */
data class CastFailure(val reason: Reason, val address: CastServerAddress?) {
    enum class Reason {
        /** The LOAD was acknowledged and no request reached the phone's LAN server in time. */
        RECEIVER_NEVER_FETCHED,

        /** The receiver reported an error (`IDLE_REASON_ERROR` or `onMediaError`) after the LOAD. */
        RECEIVER_ERROR,
    }
}
