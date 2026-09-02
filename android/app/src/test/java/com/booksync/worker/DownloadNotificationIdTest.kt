package com.booksync.worker

import java.io.File
import java.util.UUID
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Concurrent downloads need separate notifications (issue #219).
 *
 * `DownloadWorker` used a hardcoded `notificationId = 1994` for both the
 * progress notification and its `ForegroundInfo`. Download two books at once and
 * the second `setForeground` replaces the first: one notification flips between
 * titles and percentages, and finishing either one dismisses the other's
 * progress. Deriving the id from the work id gives each worker its own.
 */
class DownloadNotificationIdTest {

    @Test
    fun `two downloads get two notifications`() {
        val a = notificationIdFor(UUID.fromString("00000000-0000-0000-0000-0000000000aa"))
        val b = notificationIdFor(UUID.fromString("00000000-0000-0000-0000-0000000000bb"))
        assertNotEquals("concurrent downloads must not share a notification id", a, b)
    }

    @Test
    fun `the same work keeps the same notification`() {
        // getForegroundInfo and the progress updates are separate calls; if they
        // disagreed the progress bar would post beside the foreground
        // notification instead of updating it.
        val id = UUID.fromString("00000000-0000-0000-0000-0000000000cc")
        assertEquals(notificationIdFor(id), notificationIdFor(id))
    }

    @Test
    fun `ids stay positive`() {
        // A negative or zero id is legal for notify() but makes logs and
        // dumpsys output needlessly confusing, and 0 collides with the common
        // "unset" sentinel.
        repeat(200) {
            val id = notificationIdFor(UUID.randomUUID())
            assert(id > 0) { "notification id must be positive, was $id" }
        }
    }

    // ------------------------------------------------------------------
    // Wiring (issue #219): the policy and the id helper are both pure, so
    // both files pass in full while DownloadWorker still hardcodes 1994 and
    // returns Result.failure() for everything.
    // ------------------------------------------------------------------

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath")
    }

    @Test
    fun `the worker retries transient failures and derives its notification id`() {
        val worker = source("com/booksync/worker/DownloadWorker.kt")
        assertTrue(
            "DownloadWorker must classify failures — otherwise a blip still kills the job.",
            worker.contains("classifyDownloadFailure("),
        )
        assertTrue(
            "DownloadWorker must be able to return Result.retry().",
            worker.contains("Result.retry()"),
        )
        assertTrue(
            "the notification id must come from the work id, not a constant.",
            worker.contains("notificationIdFor("),
        )
        assertTrue(
            "the hardcoded notification id must be gone.",
            !Regex("""notificationId\s*=\s*1994""").containsMatchIn(worker),
        )
    }

    @Test
    fun `no enqueue site hand-builds a download request`() {
        // Eight sites across seven files, none of which set a backoff policy.
        // A new hand-rolled request would silently opt out of the retry backoff.
        val offenders = mutableListOf<String>()
        for (path in listOf(
            "com/booksync/ui/home/HomeViewModel.kt",
            "com/booksync/ui/library/LibraryViewModel.kt",
            "com/booksync/ui/library/SearchScreen.kt",
            "com/booksync/ui/details/BookDetailsViewModel.kt",
            "com/booksync/ui/downloaded/DownloadedViewModel.kt",
            "com/booksync/ui/player/PlayerScreen.kt",
            "com/booksync/ui/reader/ReaderScreen.kt",
        )) {
            if (source(path).contains("OneTimeWorkRequestBuilder<DownloadWorker>")) {
                offenders += path.substringAfterLast('/')
            }
        }
        assertTrue(
            "these still build their own download request instead of using " +
                "DownloadWorker.request(), so they get no retry backoff: $offenders",
            offenders.isEmpty(),
        )
    }
}
