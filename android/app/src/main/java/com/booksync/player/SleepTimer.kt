package com.booksync.player

import kotlinx.coroutines.delay

/**
 * When the sleep timer fires and how it fades (issue #225).
 *
 * The timer waits [waitMs], then steps the volume down over [fadeSteps] steps
 * of [fadeIntervalMs] each and pauses. The fade is the last 30 seconds of the
 * timer, so a ten-minute timer is nine and a half minutes of silence-free
 * listening and then a slow dim — not an abrupt stop mid-sentence.
 */
data class SleepTimerSchedule(
    val waitMs: Long,
    val fadeSteps: Int,
    val fadeIntervalMs: Long,
) {
    /** Volume for fade step [step], counting down from [fadeSteps] (full) to 0 (silent). */
    fun volumeAt(step: Int): Float = 1.0f * step / fadeSteps

    companion object {
        private const val FADE_MS = 30_000L
        private const val FADE_STEPS = 30

        /** Null means "cancel the timer" — that is what the UI sends for 0. */
        fun forMinutes(minutes: Int): SleepTimerSchedule? {
            if (minutes <= 0) return null
            val totalMs = minutes * 60 * 1000L
            val fadeDurationMs = minOf(FADE_MS, totalMs)
            return SleepTimerSchedule(
                waitMs = maxOf(0L, totalMs - FADE_MS),
                fadeSteps = FADE_STEPS,
                fadeIntervalMs = fadeDurationMs / FADE_STEPS,
            )
        }
    }
}

/** The slice of `Player` the timer touches, so a test can stand in for it. */
interface SleepTimerTarget {
    val isPlaying: Boolean
    var volume: Float
    fun pause()
}

/**
 * Runs one sleep timer to completion; cancel the coroutine to cancel the
 * timer. [target] is resolved *after* the wait, because the session's player
 * can be swapped (Cast) while the timer is counting down.
 *
 * If playback stops on its own during the fade — the user paused, audio focus
 * was lost — the fade bails without pausing. Pinned as-is: it also leaves the
 * volume wherever the fade had got to, which is pre-existing behaviour and is
 * a candidate fix, not something this refactor changes.
 */
suspend fun runSleepTimer(schedule: SleepTimerSchedule, target: () -> SleepTimerTarget?) {
    delay(schedule.waitMs)
    val player = target() ?: return
    for (step in schedule.fadeSteps downTo 0) {
        if (!player.isPlaying) return
        player.volume = schedule.volumeAt(step)
        delay(schedule.fadeIntervalMs)
    }
    player.pause()
    player.volume = 1.0f
}
