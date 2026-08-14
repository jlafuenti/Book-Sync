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
     */
    private var hasPlayed = false

    init {
        delegate.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (isPlaying) hasPlayed = true
            }

            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                // New book (or a re-load of this one): the next play is a fresh
                // start again, not a resume.
                hasPlayed = false
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
        if (playWhenReady && hasPlayed && !getPlayWhenReady()) {
            seekTo(PlaybackOffsets.resumePosition(currentPosition))
        }
        super.setPlayWhenReady(playWhenReady)
    }

    override fun play() {
        setPlayWhenReady(true)
    }
}
