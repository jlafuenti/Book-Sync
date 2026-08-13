package com.booksync

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wiring guards for two bugs that were invisible to every other kind of test:
 * code that was *correct* but never *called*.
 *
 *  1. `SyncWorker.enqueuePeriodicSync` / `triggerImmediateSync` existed, were
 *     correct, and had zero call sites — so the offline `pending_sync` queue
 *     only drained if the user happened to open the Library screen.
 *  2. `PlayerViewModel` ran a 5-second save loop identical to
 *     `AudioPlayerService`'s, so every heartbeat produced two server writes
 *     that then raced each other into 409s.
 *
 * Neither is reachable from a JVM unit test: one lives in `Application.onCreate`
 * and the other in a `MediaController` polling loop, and this module has no
 * Robolectric or `work-testing` dependency to drive them. Rather than add a
 * heavyweight dependency for two assertions, these read the source. That is a
 * blunt instrument — it pins the call, not the behaviour — but the failure mode
 * here is precisely "the call disappeared", which it catches exactly.
 *
 * If Robolectric ever arrives, replace these with a real
 * `WorkManagerTestInitHelper` assertion on the enqueued unique work.
 */
class SyncWiringTest {

    private fun source(relativePath: String): String {
        // Gradle runs unit tests with the module directory as the working dir,
        // but walk upward anyway so this survives being run from the repo root.
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    /** Comment lines don't count as call sites — that is how this regressed. */
    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    @Test
    fun `the application schedules the sync worker on startup`() {
        val app = codeLines(source("com/booksync/BookSyncApp.kt"))

        assertTrue(
            "BookSyncApp must call SyncWorker.enqueuePeriodicSync(this) — without it " +
                "nothing ever schedules SyncWorker and offline writes only replay " +
                "when the Library screen happens to open.",
            app.any { it.contains("SyncWorker.enqueuePeriodicSync") },
        )
        assertTrue(
            "BookSyncApp must also call SyncWorker.triggerImmediateSync(this) so a " +
                "queue built up while offline drains as soon as the device is back " +
                "online, rather than waiting up to 15 minutes for the periodic run.",
            app.any { it.contains("SyncWorker.triggerImmediateSync") },
        )
        assertTrue(
            "The immediate sync must hang off NetworkMonitor.isOnline, not fire " +
                "unconditionally at startup: an unconditional call races the " +
                "periodic run that also starts then, and the two replay the whole " +
                "queue twice over.",
            app.any { it.contains("networkMonitor.isOnline") },
        )
    }

    @Test
    fun `draining the offline queue is serialised`() {
        val repo = codeLines(source("com/booksync/data/repository/BookSyncRepository.kt"))

        // SyncWorker (periodic + connectivity-triggered) and LibraryViewModel can
        // all call processPendingSync at once, and each reads the whole queue up
        // front. Observed on a device: 713 replays of a 366-row queue.
        assertTrue(
            "processPendingSync must hold pendingSyncMutex for its whole body, or " +
                "concurrent callers replay the same rows against the server.",
            repo.any { it.contains("suspend fun processPendingSync() = pendingSyncMutex.withLock") },
        )
    }

    @Test
    fun `only the service runs a periodic position save`() {
        val screen = codeLines(source("com/booksync/ui/player/PlayerScreen.kt"))

        // The screen may still save at boundaries it alone can recognise (a
        // deliberate pause, switching to the reader). What it must not do is
        // run its own repeating timer alongside the service's.
        val heartbeatState = screen.filter {
            it.contains("SAVE_INTERVAL_MS") || it.contains("lastSaveTimeMs") ||
                it.contains("LOG_INTERVAL_MS") || it.contains("lastLogTimeMs")
        }
        assertEquals(
            "PlayerScreen must not keep heartbeat / 30-min-tick timer state: " +
                "AudioPlayerService.startAutoPositionSave owns the one periodic " +
                "save, and a second loop doubles every write and makes the app " +
                "race itself into 409s. Found: $heartbeatState",
            emptyList<String>(),
            heartbeatState,
        )
    }

    @Test
    fun `the service seeds its continuous-playback timer instead of comparing against zero`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))

        assertTrue(
            "AudioPlayerService must drive the 30-min history tick through " +
                "ContinuousPlaybackLog, which represents 'never started' as null. " +
                "A bare `0L` seed compares against the whole Unix epoch, so the " +
                "tick fired on the first heartbeat of every session.",
            service.any { it.contains("continuousPlaybackLog.isDue") },
        )
        assertTrue(
            "The timer must be seeded when playback starts, or the first tick is due immediately.",
            service.any { it.contains("continuousPlaybackLog.onPlaybackStarted") },
        )
        assertTrue(
            "No stray `0L`-seeded log timer should remain.",
            service.none { it.contains("lastAutoLogTimeMs") },
        )
    }
}
