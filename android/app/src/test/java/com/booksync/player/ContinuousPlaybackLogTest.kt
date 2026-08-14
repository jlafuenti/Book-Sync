package com.booksync.player

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The 30-minute continuous-playback history tick.
 *
 * The bug this pins: the timer used to be a bare `var lastLogTimeMs = 0L`
 * compared against wall-clock time, so `now - 0 >= THIRTY_MINUTES` was true on
 * the very first tick of every session. Each playback start therefore emitted
 * a spurious "30 minutes of continuous playback" history entry — observed on a
 * real device as an `append_to_log=true` write landing 5ms after the first
 * heartbeat, at the identical position.
 */
class ContinuousPlaybackLogTest {

    private val interval = 30L * 60L * 1000L
    private fun ticker() = ContinuousPlaybackLog(interval)

    @Test
    fun `is not due before playback has started`() {
        // The regression: an unseeded timer must not read as "30 minutes have
        // elapsed" just because the epoch is a large number.
        assertFalse(ticker().isDue(nowMs = 1_786_580_860_250L))
    }

    @Test
    fun `is not due immediately after playback starts`() {
        val t = ticker()
        t.onPlaybackStarted(1_000_000L)
        assertFalse(t.isDue(1_000_000L))
        assertFalse(t.isDue(1_000_000L + 5_000L))
    }

    @Test
    fun `is not due one millisecond short of the interval`() {
        val t = ticker()
        t.onPlaybackStarted(1_000_000L)
        assertFalse(t.isDue(1_000_000L + interval - 1))
    }

    @Test
    fun `is due once the interval has elapsed`() {
        val t = ticker()
        t.onPlaybackStarted(1_000_000L)
        assertTrue(t.isDue(1_000_000L + interval))
    }

    @Test
    fun `recording an entry restarts the interval`() {
        val t = ticker()
        t.onPlaybackStarted(1_000_000L)
        val due = 1_000_000L + interval
        assertTrue(t.isDue(due))

        t.onLogged(due)
        assertFalse(t.isDue(due))
        assertFalse(t.isDue(due + interval - 1))
        assertTrue(t.isDue(due + interval))
    }

    @Test
    fun `resuming after a pause does not restart the interval`() {
        // Pauses freeze the clock by tearing the heartbeat loop down, and any
        // pause that logs calls onLogged. Resuming must not reseed on top of
        // that, or a listener who pauses every few minutes would never reach
        // an entry at all.
        val t = ticker()
        t.onPlaybackStarted(1_000_000L)
        val resumed = 1_000_000L + interval - 1_000L
        t.onPlaybackStarted(resumed) // resume: already running, no reseed

        assertTrue(t.isDue(1_000_000L + interval))
    }

    @Test
    fun `an entry logged at a boundary seeds a timer that never started`() {
        // Boundary saves (pause, natural end) can log before any heartbeat has
        // run. That must leave the timer seeded, not still-unstarted.
        val t = ticker()
        t.onLogged(2_000_000L)

        assertFalse(t.isDue(2_000_000L))
        assertTrue(t.isDue(2_000_000L + interval))
    }
}
