package com.booksync.player

import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.Player

/**
 * Backs playback up by [PlaybackOffsets.RESUME_REWIND_MS] whenever it resumes
 * from a pause, so you don't pick up mid-word (issue #42).
 *
 * This sits on the *session* player rather than in a button handler on purpose.
 * The phone player used to do the rewind itself inside `togglePlayback`, which
 * meant Android Auto, the notification, headset buttons, Bluetooth, and
 * audio-focus recovery after a nav prompt — all of which drive the MediaSession
 * player directly — got no rewind at all. One wrapper covers every one of them.
 *
 * Not applied to Cast. [AudioPlayerService.switchToPlayer] branches on
 * `newPlayer is CastPlayer`, so wrapping the CastPlayer would silently break the
 * Cast handoff; the CastPlayer is handed to the session unwrapped.
 */
class ResumeRewindPlayer(delegate: Player) : ForwardingPlayer(delegate) {

    /**
     * Whether playback has actually run since the current item was loaded.
     *
     * Opening a book restores its saved position and starts playing — that is a
     * fresh start, not a resume, and rewinding it would cost 5s every time the
     * app is opened. Only once playback has been heard does a subsequent
     * paused→playing transition count as a resume.
     *
     * Exposed for the service's seek flush (issue #166): a REASON_SEEK
     * discontinuity before playback has been heard is the restore seek at
     * open, not a user scrub, and must not be flushed (let alone claim the
     * format).
     */
    var hasPlayedSinceItemTransition = false
        private set

    /**
     * One-shot marker for the wrapper's own rewind seek (issue #166): set just
     * before [setPlayWhenReady] issues it, consumed by the service's
     * onPositionDiscontinuity so the programmatic rewind is not flushed as if
     * the user had scrubbed. Both run in order on the application looper.
     */
    private var rewindSeekInFlight = false

    fun consumeResumeRewindSeek(): Boolean {
        val wasRewind = rewindSeekInFlight
        rewindSeekInFlight = false
        return wasRewind
    }

    /**
     * One-shot (issue #772): the next paused-to-playing request does not
     * rewind. The reader's read-along start seeks to the exact sentence it
     * wants and then plays; the usual resume rewind would back that deliberate
     * seek up by another [PlaybackOffsets.RESUME_REWIND_MS]. Armed by
     * `AudioPlayerService.CMD_SUPPRESS_NEXT_RESUME_REWIND`, spent by the next
     * resume candidate (a first play after a load is not a resume and would not
     * rewind anyway, but it still spends it, so a skip can never linger and
     * swallow the rewind of a later, ordinary resume), dropped when another
     * item loads. Setting playWhenReady to true while it already is true is no
     * resume and leaves it armed.
     */
    fun suppressNextResumeRewind() {
        suppressNextRewind = true
    }

    private var suppressNextRewind = false

    init {
        delegate.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (isPlaying) hasPlayedSinceItemTransition = true
            }

            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                // New book (or a re-load of this one): the next play is a fresh
                // start again, not a resume.
                hasPlayedSinceItemTransition = false
                suppressNextRewind = false
            }
        })
    }

    /**
     * The single place the rewind is applied. [play] routes through here rather
     * than through `super.play()` because `ForwardingPlayer.play()` is itself
     * implemented as `setPlayWhenReady(true)` — going through super would run
     * this method twice and jump back 10s instead of 5s.
     */
    override fun setPlayWhenReady(playWhenReady: Boolean) {
        if (playWhenReady && !getPlayWhenReady()) {
            val skipRewind = suppressNextRewind
            suppressNextRewind = false
            if (hasPlayedSinceItemTransition && !skipRewind) {
                rewindSeekInFlight = true
                seekTo(PlaybackOffsets.resumePosition(currentPosition))
            }
        }
        super.setPlayWhenReady(playWhenReady)
    }

    override fun play() {
        setPlayWhenReady(true)
    }
}
