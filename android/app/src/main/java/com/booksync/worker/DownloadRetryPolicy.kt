package com.booksync.worker

import java.io.IOException

/** What to do with a failed download attempt. */
enum class DownloadOutcome { Retry, Fail }

/** WorkManager gives up after this many attempts; mirrors `SyncWorker`'s `runAttemptCount < 5`. */
private const val MAX_ATTEMPTS = 5

/**
 * Whether a failed download deserves another attempt (issue #219).
 *
 * [DownloadWorker] used to answer `Result.failure()` for everything, so a
 * momentary blip partway through a several-hundred-megabyte audiobook killed
 * the job for good — the user had to spot an error chip and start again from
 * byte zero. `SyncWorker` has retried since it was written; the worker doing
 * the long, failure-prone transfers never did.
 *
 * Not a blanket retry, though: a 404, a 401 or a book missing from the local
 * database fails identically five times over, just slower and with five
 * foreground services along the way. So transient causes retry and definite
 * ones stop.
 *
 * @param runAttemptCount WorkManager's zero-based attempt counter
 */
fun classifyDownloadFailure(
    e: Throwable,
    runAttemptCount: Int,
    maxAttempts: Int = MAX_ATTEMPTS,
): DownloadOutcome {
    if (runAttemptCount >= maxAttempts) return DownloadOutcome.Fail

    // Retrofit and okio wrap the real cause; matching only the top-level type
    // would read a genuine network blip as permanent.
    val causes = generateSequence(e) { it.cause }.take(8)
    if (causes.any { it is IOException }) return DownloadOutcome.Retry

    // The repository reports HTTP failures as Exception("HTTP <code>: …")
    // (BookSyncRepository:560), so the status has to come out of the message.
    val status = causes.firstNotNullOfOrNull { c ->
        Regex("""HTTP (\d{3})""").find(c.message.orEmpty())?.groupValues?.get(1)?.toIntOrNull()
    }
    if (status != null) {
        // 5xx is the server having a bad moment; 4xx is about this request and
        // will not improve on its own.
        return if (status in 500..599) DownloadOutcome.Retry else DownloadOutcome.Fail
    }

    // Anything unrecognised is likelier a bug than a blip. Failing keeps it off
    // the user's battery and data, and surfaces it instead of hiding it behind
    // four silent retries.
    return DownloadOutcome.Fail
}

/**
 * A notification id unique to one download (issue #219).
 *
 * [DownloadWorker] used a hardcoded 1994 for both the progress notification and
 * its `ForegroundInfo`, so two concurrent downloads shared one notification: the
 * second `setForeground` replaced the first, the title flipped between books,
 * and finishing either dismissed the other's progress.
 *
 * Kept positive and away from zero, which reads as "unset" in most notification
 * code, and stable for a given work id so the progress updates land on the same
 * notification the foreground service posted.
 */
fun notificationIdFor(workId: java.util.UUID): Int =
    (workId.hashCode().toLong().and(0x7FFFFFFF).toInt()).coerceAtLeast(1)
