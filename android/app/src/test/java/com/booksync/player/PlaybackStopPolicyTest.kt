package com.booksync.player

import androidx.media3.common.Player
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which not-playing moments are a stop (issue #766).
 *
 * Media3 reports `isPlaying = false` on every rebuffer, and the service used to
 * treat each one as a pause: stop the heartbeat, push a position, append a
 * history entry. A stalling stream flapped READY/BUFFERING about once a second
 * and pushed on every flap. A stall is not a stop; a pause pressed *during* a
 * stall is, even though `isPlaying` never changes for it.
 */
class PlaybackStopPolicyTest {

    private fun isStop(
        heartbeatRunning: Boolean = true,
        isPlaying: Boolean = false,
        playbackState: Int = Player.STATE_READY,
        playWhenReady: Boolean = false,
    ) = PlaybackStopPolicy.isStop(heartbeatRunning, isPlaying, playbackState, playWhenReady)

    @Test
    fun `a rebuffer while the user still wants playback is not a stop`() {
        assertFalse(isStop(playbackState = Player.STATE_BUFFERING, playWhenReady = true))
    }

    @Test
    fun `a pause pressed during a stall is a stop`() {
        // isPlaying was already false, so no onIsPlayingChanged fires for it;
        // only playWhenReady drops.
        assertTrue(isStop(playbackState = Player.STATE_BUFFERING, playWhenReady = false))
    }

    @Test
    fun `an ordinary pause is a stop`() {
        assertTrue(isStop(playbackState = Player.STATE_READY, playWhenReady = false))
    }

    @Test
    fun `audio focus loss is a stop`() {
        // READY and still requested, but suppressed: nothing is playing.
        assertTrue(isStop(playbackState = Player.STATE_READY, playWhenReady = true))
    }

    @Test
    fun `a playback error is a stop`() {
        assertTrue(isStop(playbackState = Player.STATE_IDLE, playWhenReady = true))
    }

    @Test
    fun `the end of the book is a stop`() {
        assertTrue(isStop(playbackState = Player.STATE_ENDED, playWhenReady = true))
    }

    @Test
    fun `nothing is a stop while audio is playing`() {
        assertFalse(isStop(isPlaying = true, playbackState = Player.STATE_READY, playWhenReady = true))
    }

    @Test
    fun `nothing is a stop unless playback was running`() {
        // The initial load (BUFFERING before the first sound), and every
        // further callback after a stop was already handled: one stop, one save.
        assertFalse(isStop(heartbeatRunning = false, playbackState = Player.STATE_READY, playWhenReady = false))
        assertFalse(isStop(heartbeatRunning = false, playbackState = Player.STATE_IDLE, playWhenReady = true))
    }
}
