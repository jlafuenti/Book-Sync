package com.booksync.player

import androidx.media3.common.Player
import com.google.android.gms.cast.MediaStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Cast handoff's decisions (issue #225): where the new player starts and
 * whether it starts playing, plus the two bits of Cast-status bookkeeping the
 * `RemoteMediaClient` callback does. Before this they were inline in
 * `AudioPlayerService.switchToPlayer`, verified only with a real Chromecast.
 */
class PlayerSwitchDecisionTest {

    // --- Position ---

    @Test
    fun `leaving Cast with a zero position falls back to what the receiver last reported`() {
        // CastPlayer.currentPosition reads 0 once the session has ended — it has
        // already disconnected — so local playback would restart at 0:00.
        val d = decidePlayerSwitch(
            leavingCast = true, rawPositionMs = 0L, lastKnownCastPositionMs = 123_000L,
            playWhenReady = true, playbackState = Player.STATE_READY,
        )
        assertEquals(123_000L, d.positionMs)
    }

    @Test
    fun `leaving Cast with a real position keeps it`() {
        val d = decidePlayerSwitch(
            leavingCast = true, rawPositionMs = 45_000L, lastKnownCastPositionMs = 123_000L,
            playWhenReady = true, playbackState = Player.STATE_READY,
        )
        assertEquals(45_000L, d.positionMs)
    }

    @Test
    fun `leaving Cast with nothing ever reported stays at zero`() {
        val d = decidePlayerSwitch(
            leavingCast = true, rawPositionMs = 0L, lastKnownCastPositionMs = 0L,
            playWhenReady = true, playbackState = Player.STATE_READY,
        )
        assertEquals(0L, d.positionMs)
    }

    @Test
    fun `going to Cast never substitutes a stale receiver position`() {
        val d = decidePlayerSwitch(
            leavingCast = false, rawPositionMs = 0L, lastKnownCastPositionMs = 123_000L,
            playWhenReady = true, playbackState = Player.STATE_READY,
        )
        assertEquals(0L, d.positionMs)
    }

    // --- Autoplay ---

    @Test
    fun `playback continues on the new player only if it was playing and not ended`() {
        fun play(playWhenReady: Boolean, state: Int) = decidePlayerSwitch(
            leavingCast = false, rawPositionMs = 1L, lastKnownCastPositionMs = 0L,
            playWhenReady = playWhenReady, playbackState = state,
        ).shouldPlay
        assertTrue(play(true, Player.STATE_READY))
        assertTrue(play(true, Player.STATE_BUFFERING))
        assertFalse(play(false, Player.STATE_READY))
        assertFalse(play(true, Player.STATE_ENDED))
    }

    // --- Receiver status bookkeeping ---

    @Test
    fun `a positive reported position replaces the remembered one, zero does not`() {
        assertEquals(500L, castReportedPosition(previousMs = 100L, reportedMs = 500L))
        assertEquals(100L, castReportedPosition(previousMs = 100L, reportedMs = 0L))
        assertEquals(100L, castReportedPosition(previousMs = 100L, reportedMs = -1L))
    }

    @Test
    fun `status labels name the known states and fall back to the raw code`() {
        assertEquals("PLAYING idle=none", castStatusLabel(MediaStatus.PLAYER_STATE_PLAYING, MediaStatus.IDLE_REASON_NONE))
        assertEquals("IDLE idle=FINISHED", castStatusLabel(MediaStatus.PLAYER_STATE_IDLE, MediaStatus.IDLE_REASON_FINISHED))
        assertEquals("PAUSED idle=CANCELED", castStatusLabel(MediaStatus.PLAYER_STATE_PAUSED, MediaStatus.IDLE_REASON_CANCELED))
        assertEquals("BUFFERING idle=INTERRUPTED", castStatusLabel(MediaStatus.PLAYER_STATE_BUFFERING, MediaStatus.IDLE_REASON_INTERRUPTED))
        assertEquals("LOADING idle=ERROR", castStatusLabel(MediaStatus.PLAYER_STATE_LOADING, MediaStatus.IDLE_REASON_ERROR))
        assertEquals("UNKNOWN idle=none", castStatusLabel(MediaStatus.PLAYER_STATE_UNKNOWN, MediaStatus.IDLE_REASON_NONE))
        assertEquals("state=42 idle=reason=17", castStatusLabel(42, 17))
    }
}
