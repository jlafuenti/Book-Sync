package com.booksync.player

import androidx.media3.common.Player
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Rewind-on-resume for every playback surface (issue #42).
 *
 * The phone player used to do this rewind itself, inside its own play/pause
 * button handler, so Android Auto, the notification, and headset buttons — which
 * talk to the MediaSession player directly and never touch that handler — resumed
 * exactly mid-word. Moving it into the session player covers all of them at once.
 *
 * Two regressions are pinned here:
 *
 *  - **Double rewind.** `ForwardingPlayer.play()` is implemented as
 *    `setPlayWhenReady(true)`, so a naive override of both that delegates through
 *    `super.play()` rewinds twice and jumps back 10s instead of 5s.
 *  - **Rewinding the first play.** Opening a book restores its saved position and
 *    starts playing; that is not a resume-from-pause and must not lose 5s every
 *    time the app is opened.
 */
class ResumeRewindPlayerTest {

    private lateinit var delegate: Player
    private lateinit var player: ResumeRewindPlayer
    private lateinit var listener: Player.Listener

    @Before
    fun setUp() {
        delegate = mockk(relaxed = true)
        val captured = slot<Player.Listener>()
        player = ResumeRewindPlayer(delegate)
        verify { delegate.addListener(capture(captured)) }
        listener = captured.captured
    }

    /** Put the player in the state it reaches after playback has started once. */
    private fun startPlayingAt(positionMs: Long) {
        listener.onIsPlayingChanged(true)
        every { delegate.currentPosition } returns positionMs
        every { delegate.playWhenReady } returns false
    }

    @Test
    fun `play from a pause rewinds by the resume offset`() {
        startPlayingAt(90_000L)

        player.play()

        verify(exactly = 1) { delegate.seekTo(85_000L) }
        verify { delegate.playWhenReady = true }
    }

    @Test
    fun `setPlayWhenReady true from a pause rewinds exactly once`() {
        // The double-rewind regression: this is the path play() routes through,
        // and one resume must produce exactly one seek.
        startPlayingAt(90_000L)

        player.playWhenReady = true

        verify(exactly = 1) { delegate.seekTo(any<Long>()) }
        verify(exactly = 1) { delegate.seekTo(85_000L) }
    }

    @Test
    fun `resume clamps at the start of the file`() {
        startPlayingAt(2_000L)

        player.play()

        verify(exactly = 1) { delegate.seekTo(0L) }
    }

    @Test
    fun `the first play of a book does not rewind`() {
        // No onIsPlayingChanged(true) yet — this is a fresh open, restoring a
        // saved position, not a resume.
        every { delegate.currentPosition } returns 90_000L
        every { delegate.playWhenReady } returns false

        player.play()

        verify(exactly = 0) { delegate.seekTo(any<Long>()) }
        verify { delegate.playWhenReady = true }
    }

    @Test
    fun `switching to another book resets the resume state`() {
        startPlayingAt(90_000L)
        listener.onMediaItemTransition(null, Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED)

        player.play()

        verify(exactly = 0) { delegate.seekTo(any<Long>()) }
    }

    @Test
    fun `setting playWhenReady true while already playing does not rewind`() {
        listener.onIsPlayingChanged(true)
        every { delegate.currentPosition } returns 90_000L
        every { delegate.playWhenReady } returns true

        player.playWhenReady = true

        verify(exactly = 0) { delegate.seekTo(any<Long>()) }
    }

    @Test
    fun `pausing does not rewind`() {
        startPlayingAt(90_000L)

        player.playWhenReady = false

        verify(exactly = 0) { delegate.seekTo(any<Long>()) }
        verify { delegate.playWhenReady = false }
    }

    @Test
    fun `pause() does not rewind`() {
        startPlayingAt(90_000L)
        every { delegate.playWhenReady } returns true

        player.pause()

        verify(exactly = 0) { delegate.seekTo(any<Long>()) }
    }

    @Test
    fun `wraps the delegate it was given`() {
        assertTrue(player.wrappedPlayer === delegate)
    }

    // ============ Seek-flush support (issue #166) ============
    //
    // The service flushes the position on user seeks via
    // onPositionDiscontinuity(REASON_SEEK), with claimFormat=true — the claim
    // rule allows an explicit user command to claim the format. The wrapper's
    // own resume rewind and the restore seek at open are NOT user commands:
    // flushing them would claim "audiobook" on a mere screen-open, the exact
    // hijack the rule forbids. The wrapper exposes what the service needs to
    // tell them apart.

    @Test
    fun `the resume rewind seek is flagged as programmatic, consumed once`() {
        startPlayingAt(90_000L)

        player.play()

        assertTrue("the rewind's own seek must be flagged", player.consumeResumeRewindSeek())
        assertFalse("the flag is one-shot", player.consumeResumeRewindSeek())
    }

    @Test
    fun `a user seek is not flagged as the resume rewind`() {
        startPlayingAt(90_000L)

        player.seekTo(50_000L)

        assertFalse(player.consumeResumeRewindSeek())
    }

    @Test
    fun `exposes whether playback has been heard since the item loaded`() {
        // False at open (a discontinuity now is the restore seek, not a user
        // scrub); true once heard; false again when a new item loads.
        assertFalse(player.hasPlayedSinceItemTransition)

        listener.onIsPlayingChanged(true)
        assertTrue(player.hasPlayedSinceItemTransition)

        listener.onMediaItemTransition(null, Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED)
        assertFalse(player.hasPlayedSinceItemTransition)
    }
}
