package com.booksync.player

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The fail-fast decision (issue #932): after a Cast LOAD is acknowledged, has
 * the receiver come to fetch the file from the phone? Pure, driven with
 * explicit timestamps; `AudioPlayerService` only wires the clock in and the
 * outcome out.
 */
class CastFetchWatchdogTest {

    private val window = 8_000L

    @Test
    fun `nothing is decided before a LOAD is acknowledged`() {
        val w = CastFetchWatchdog(window)
        assertEquals(CastFetchVerdict.WAITING, w.verdict(0))
        assertEquals(CastFetchVerdict.WAITING, w.verdict(1_000_000))
    }

    @Test
    fun `a request inside the window after the ack means the receiver fetched`() {
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        w.onReceiverRequest(3_000)
        assertEquals(CastFetchVerdict.FETCHED, w.verdict(3_000))
        assertEquals("and it stays fetched past the window", CastFetchVerdict.FETCHED, w.verdict(60_000))
    }

    @Test
    fun `an ack with no request is waiting until the window closes, then timed out`() {
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        assertEquals(CastFetchVerdict.WAITING, w.verdict(1_000))
        assertEquals(CastFetchVerdict.WAITING, w.verdict(8_999))
        assertEquals(CastFetchVerdict.TIMED_OUT, w.verdict(9_000))
        assertEquals(CastFetchVerdict.TIMED_OUT, w.verdict(20_000))
    }

    @Test
    fun `a receiver error after the ack is a receiver error, even inside the window`() {
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        w.onReceiverError()
        assertEquals(CastFetchVerdict.RECEIVER_ERROR, w.verdict(1_500))
    }

    @Test
    fun `a receiver error with no ack is ignored`() {
        val w = CastFetchWatchdog(window)
        w.onReceiverError()
        assertEquals(CastFetchVerdict.WAITING, w.verdict(0))
    }

    @Test
    fun `a request that arrived before the ack counts for that ack`() {
        // The receiver may probe the URL before its LOAD acknowledgement reaches
        // the phone.
        val w = CastFetchWatchdog(window)
        w.onReceiverRequest(500)
        w.onLoadAcked(1_000)
        assertEquals(CastFetchVerdict.FETCHED, w.verdict(1_000))
        assertEquals(CastFetchVerdict.FETCHED, w.verdict(30_000))
    }

    @Test
    fun `a second ack restarts the window and forgets the earlier fetch`() {
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        w.onReceiverRequest(2_000)
        w.onLoadAcked(20_000)
        assertEquals(CastFetchVerdict.WAITING, w.verdict(27_999))
        assertEquals(CastFetchVerdict.TIMED_OUT, w.verdict(28_000))
    }

    @Test
    fun `a request after a second ack satisfies that ack`() {
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        w.onLoadAcked(5_000)
        w.onReceiverRequest(6_000)
        assertEquals(CastFetchVerdict.FETCHED, w.verdict(30_000))
    }

    @Test
    fun `a receiver error after a fetch is still an error`() {
        // The request proved the phone is reachable; an error after it is the
        // receiver rejecting the audio, which is a failed cast all the same.
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        w.onReceiverRequest(2_000)
        w.onReceiverError()
        assertEquals(CastFetchVerdict.RECEIVER_ERROR, w.verdict(2_100))
    }

    @Test
    fun `reset forgets everything`() {
        val w = CastFetchWatchdog(window)
        w.onLoadAcked(1_000)
        w.reset()
        assertEquals(CastFetchVerdict.WAITING, w.verdict(100_000))
    }

    @Test
    fun `the default window is eight seconds`() {
        assertEquals(8_000L, CastFetchWatchdog().windowMs)
    }
}
