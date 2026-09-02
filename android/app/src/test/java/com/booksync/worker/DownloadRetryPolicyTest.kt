package com.booksync.worker

import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Whether a failed download is worth another attempt (issue #219).
 *
 * `DownloadWorker` returned `Result.failure()` for everything, so a momentary
 * blip partway through a multi-hundred-megabyte audiobook killed the job
 * permanently: no retry, and the user had to notice an error chip and start
 * again from byte zero. `SyncWorker` has retried since it was written
 * (`runAttemptCount < 5`); the download worker — the one doing the long,
 * failure-prone transfers — never did.
 *
 * Retrying is not unconditionally right, which is why this is a decision rather
 * than a blanket `Result.retry()`: a 404 or a book missing from the local DB
 * will fail identically five times, just slower and with five foreground
 * services.
 *
 * The HTTP cases arrive as a plain `Exception("HTTP 503: …")` from
 * `BookSyncRepository:560`, so the status has to be read out of the message.
 */
class DownloadRetryPolicyTest {

    @Test
    fun `a network blip early on is retried`() {
        assertEquals(
            DownloadOutcome.Retry,
            classifyDownloadFailure(IOException("unexpected end of stream"), runAttemptCount = 0),
        )
        assertEquals(
            DownloadOutcome.Retry,
            classifyDownloadFailure(SocketTimeoutException("timeout"), runAttemptCount = 2),
        )
        assertEquals(
            "a dropped DNS lookup mid-download is transient",
            DownloadOutcome.Retry,
            classifyDownloadFailure(UnknownHostException("tandem.lafuenti.com"), runAttemptCount = 1),
        )
    }

    @Test
    fun `retrying stops at the cap`() {
        // Without this an unreachable server would keep a foreground service
        // cycling forever on a book that is never going to arrive.
        assertEquals(
            DownloadOutcome.Fail,
            classifyDownloadFailure(IOException("still down"), runAttemptCount = 5),
        )
        assertEquals(
            DownloadOutcome.Fail,
            classifyDownloadFailure(IOException("still down"), runAttemptCount = 9),
        )
    }

    @Test
    fun `a server error is transient, a client error is not`() {
        assertEquals(
            DownloadOutcome.Retry,
            classifyDownloadFailure(Exception("HTTP 503: Service Unavailable"), runAttemptCount = 0),
        )
        assertEquals(
            DownloadOutcome.Retry,
            classifyDownloadFailure(Exception("HTTP 500: Internal Server Error"), runAttemptCount = 0),
        )
        assertEquals(
            "a 404 will be a 404 five times over",
            DownloadOutcome.Fail,
            classifyDownloadFailure(Exception("HTTP 404: Not Found"), runAttemptCount = 0),
        )
        assertEquals(
            "401 means the session is the problem, not the network",
            DownloadOutcome.Fail,
            classifyDownloadFailure(Exception("HTTP 401: Unauthorized"), runAttemptCount = 0),
        )
    }

    @Test
    fun `a book missing from the local database is not a network problem`() {
        assertEquals(
            DownloadOutcome.Fail,
            classifyDownloadFailure(Exception("Pair not found in DB"), runAttemptCount = 0),
        )
        assertEquals(
            DownloadOutcome.Fail,
            classifyDownloadFailure(Exception("Ebook not found in DB"), runAttemptCount = 0),
        )
    }

    @Test
    fun `an unrecognised failure fails rather than looping`() {
        // Deliberately conservative: an NPE or an IllegalState is a bug, and
        // running it five times just burns the user's battery and data.
        assertEquals(
            DownloadOutcome.Fail,
            classifyDownloadFailure(IllegalStateException("boom"), runAttemptCount = 0),
        )
        assertEquals(
            DownloadOutcome.Fail,
            classifyDownloadFailure(NullPointerException(), runAttemptCount = 0),
        )
    }

    @Test
    fun `an IO failure wrapped in something else is still transient`() {
        // Retrofit and okio both wrap; matching only the top-level type would
        // classify a genuine blip as a permanent failure.
        assertEquals(
            DownloadOutcome.Retry,
            classifyDownloadFailure(
                RuntimeException("write failed", IOException("ENOSPC")),
                runAttemptCount = 0,
            ),
        )
    }
}
