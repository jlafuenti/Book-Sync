package com.booksync.player

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The network-push throttle for the 5-second position heartbeat (issue #65).
 *
 * The heartbeat keeps Room fresh every 5s; the server only needs the position
 * for cross-device resume, where 30s of staleness is imperceptible. So a tick
 * writes locally always, and pushes to the server only when the throttle says
 * enough time has passed since the last *successful* push. A failed push must
 * not advance it — the next tick retries instead of waiting out another window.
 */
class HeartbeatThrottleTest {

    private val interval = 30_000L
    private fun throttle() = HeartbeatThrottle(interval)

    @Test
    fun `pushes on the very first tick of a session`() {
        // Nothing has ever been pushed: the first tick is a push, so a fresh
        // start isn't 30s behind for no reason.
        assertTrue(throttle().shouldPush(nowMs = 1_786_580_860_250L))
    }

    @Test
    fun `does not push again inside the interval`() {
        val t = throttle()
        t.onPushed(1_000_000L)
        assertFalse(t.shouldPush(1_000_000L + 5_000L))
        assertFalse(t.shouldPush(1_000_000L + 25_000L))
        assertFalse(t.shouldPush(1_000_000L + interval - 1))
    }

    @Test
    fun `pushes once the interval has elapsed`() {
        val t = throttle()
        t.onPushed(1_000_000L)
        assertTrue(t.shouldPush(1_000_000L + interval))
        assertTrue(t.shouldPush(1_000_000L + interval + 5_000L))
    }

    @Test
    fun `a push that was not confirmed does not advance the window`() {
        // The caller only reports successes; asking twice without an
        // onPushed in between (a failed PUT) keeps saying "push".
        val t = throttle()
        t.onPushed(1_000_000L)
        assertTrue(t.shouldPush(1_000_000L + interval))
        assertTrue(t.shouldPush(1_000_000L + interval + 5_000L))
    }

    @Test
    fun `reset makes the next tick push again`() {
        // A new playback session (stop/start) should not inherit the previous
        // one's window: its first tick pushes.
        val t = throttle()
        t.onPushed(1_000_000L)
        t.reset()
        assertTrue(t.shouldPush(1_000_000L + 5_000L))
    }
}
