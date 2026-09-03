package com.booksync.player

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which stop reason may claim the format (issue #226).
 *
 * `AudioPlayerService.onIsPlayingChanged(false)` is the single owner of the
 * pause position write, but the listener alone cannot tell a deliberate pause
 * from audio-focus loss, a Bluetooth disconnect or the sleep timer — so it used
 * to pass `claimFormat = false` for all of them, and the player screen ran a
 * second write of its own just to claim the format. That second write is gone;
 * the screen now sends `CMD_USER_PAUSE` before `pause()` and this policy
 * carries the intent across to the listener.
 *
 * The flag is one-shot: exactly the stop that follows the command claims.
 */
class PauseSavePolicyTest {

    @Test
    fun `a stop with no user command does not claim the format`() {
        // Audio focus loss, Bluetooth disconnect, the sleep timer — the
        // involuntary stops the old comment listed. Nothing announced them,
        // so they stay background saves.
        assertFalse(PauseSavePolicy().consumeClaimFormat())
    }

    @Test
    fun `a stop after the user pause command claims the format`() {
        val policy = PauseSavePolicy()
        policy.onUserPauseCommand()
        assertTrue(policy.consumeClaimFormat())
    }

    @Test
    fun `the flag is consumed by one stop`() {
        // One command must not license every later stop. The sleep timer
        // firing an hour after the user tapped pause is not a user command.
        val policy = PauseSavePolicy()
        policy.onUserPauseCommand()
        assertTrue(policy.consumeClaimFormat())
        assertFalse(policy.consumeClaimFormat())
    }

    @Test
    fun `repeated commands still arm exactly one stop`() {
        val policy = PauseSavePolicy()
        policy.onUserPauseCommand()
        policy.onUserPauseCommand()
        assertTrue(policy.consumeClaimFormat())
        assertFalse(policy.consumeClaimFormat())
    }

    @Test
    fun `playback starting clears a command that never produced a stop`() {
        // A pause command sent while the player was already paused fires no
        // onIsPlayingChanged, so nothing consumes the flag. Without this
        // reset it would leak forward and let the next sleep-timer stop
        // claim the format.
        val policy = PauseSavePolicy()
        policy.onUserPauseCommand()
        policy.onPlaybackStarted()
        assertFalse(policy.consumeClaimFormat())
    }
}
