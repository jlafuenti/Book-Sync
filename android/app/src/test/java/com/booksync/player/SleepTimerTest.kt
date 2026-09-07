package com.booksync.player

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The sleep timer (issue #225): a wait, then a 30-second fade to silence, then
 * a pause with the volume put back for the next play. Used to be a coroutine
 * inline in `AudioPlayerService.handleSleepTimer`, verified by waiting for it.
 * Virtual time here, so a ten-minute timer runs in milliseconds.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SleepTimerTest {

    private class FakeTarget(override var isPlaying: Boolean = true) : SleepTimerTarget {
        val volumes = mutableListOf<Float>()
        var pauses = 0
        override var volume: Float = 1.0f
            set(value) { field = value; volumes += value }
        override fun pause() { pauses++; isPlaying = false }
    }

    // --- Schedule ---

    @Test
    fun `zero or negative minutes is a cancel, not a timer`() {
        assertNull(SleepTimerSchedule.forMinutes(0))
        assertNull(SleepTimerSchedule.forMinutes(-5))
    }

    @Test
    fun `the fade takes the last 30 seconds, in 30 steps`() {
        val s = SleepTimerSchedule.forMinutes(10)!!
        assertEquals(9 * 60_000L + 30_000L, s.waitMs)
        assertEquals(30, s.fadeSteps)
        assertEquals(1_000L, s.fadeIntervalMs)
    }

    @Test
    fun `a one-minute timer waits thirty seconds then fades`() {
        val s = SleepTimerSchedule.forMinutes(1)!!
        assertEquals(30_000L, s.waitMs)
        assertEquals(1_000L, s.fadeIntervalMs)
    }

    @Test
    fun `volume steps down linearly to silence`() {
        val s = SleepTimerSchedule.forMinutes(10)!!
        assertEquals(1.0f, s.volumeAt(30), 0f)
        assertEquals(0.5f, s.volumeAt(15), 0f)
        assertEquals(0.0f, s.volumeAt(0), 0f)
    }

    // --- Running it ---

    @Test
    fun `nothing is touched until the wait is over`() = runTest {
        val target = FakeTarget()
        val schedule = SleepTimerSchedule.forMinutes(10)!!
        launch { runSleepTimer(schedule) { target } }
        advanceTimeBy(schedule.waitMs)
        assertTrue(target.volumes.isEmpty())
        assertEquals(0, target.pauses)
    }

    @Test
    fun `after the wait the volume fades to zero, then it pauses and restores full volume`() = runTest {
        val target = FakeTarget()
        launch { runSleepTimer(SleepTimerSchedule.forMinutes(10)!!) { target } }
        advanceUntilIdle()
        assertEquals(1, target.pauses)
        // 31 fade writes (30 down to 0) and the restore.
        assertEquals(32, target.volumes.size)
        assertEquals(1.0f, target.volumes.first(), 0f)
        assertEquals(0.0f, target.volumes[30], 0f)
        assertEquals(1.0f, target.volumes.last(), 0f)
        assertTrue(target.volumes.take(31).zipWithNext().all { (a, b) -> b < a })
        assertEquals(1.0f, target.volume, 0f)
    }

    @Test
    fun `a player that stopped on its own during the fade is left alone`() = runTest {
        // Pinned as-is: the fade bails without a pause. (It also leaves the
        // volume where the fade got to — pre-existing behaviour, see the note
        // on runSleepTimer.)
        val target = FakeTarget()
        val schedule = SleepTimerSchedule.forMinutes(1)!!
        launch { runSleepTimer(schedule) { target } }
        advanceTimeBy(schedule.waitMs + 5 * schedule.fadeIntervalMs + 1)
        target.isPlaying = false
        advanceUntilIdle()
        assertEquals(0, target.pauses)
        assertTrue(target.volumes.size < 31)
    }

    @Test
    fun `no player when the wait ends means nothing happens`() = runTest {
        var calls = 0
        launch { runSleepTimer(SleepTimerSchedule.forMinutes(1)!!) { calls++; null } }
        advanceUntilIdle()
        assertEquals(1, calls)
    }

    @Test
    fun `cancelling the timer during the wait touches nothing`() = runTest {
        val target = FakeTarget()
        val job = launch { runSleepTimer(SleepTimerSchedule.forMinutes(10)!!) { target } }
        advanceTimeBy(60_000L)
        job.cancel()
        advanceUntilIdle()
        assertTrue(target.volumes.isEmpty())
        assertEquals(0, target.pauses)
    }
}
