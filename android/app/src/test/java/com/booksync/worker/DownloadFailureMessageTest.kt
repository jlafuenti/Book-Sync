package com.booksync.worker

import java.io.File
import java.io.IOException
import java.net.UnknownHostException
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A download that never starts must say why (issue #338).
 *
 * Tapping "Download" on a search result for a book the local cache has never
 * seen used to end in `Result.failure("Audiobook not found in DB")` — a string
 * only logcat ever saw. These are the messages that replace it, so the wording
 * is pinned rather than left to whoever next edits the call site.
 */
class DownloadFailureMessageTest {

    @Test
    fun `a book the server no longer has names the book`() {
        val msg = downloadUnavailableMessage("Bartleby", cause = null)
        assertTrue("must name the book, was: $msg", msg.contains("Bartleby"))
        assertTrue("must say it is gone from the server, was: $msg", msg.contains("server"))
    }

    @Test
    fun `a caller that does not know the title still gets a sentence`() {
        // The reader placeholder on a cold cache has only a pair id (issue
        // #417): the title would have come from the very row that is missing.
        // "\"null\" is no longer in the library" is not a message.
        val gone = downloadUnavailableMessage(null, cause = null)
        assertTrue("must not leak null, was: $gone", !gone.contains("null"))
        assertTrue("must read as a sentence, was: $gone", gone.startsWith("This book"))
        val offline = downloadUnavailableMessage(null, UnknownHostException("no such host"))
        assertTrue("must not leak null, was: $offline", !offline.contains("null"))
        assertTrue("must mention reaching the server, was: $offline", offline.contains("reach the server"))
    }

    @Test
    fun `an unreachable server says so instead of blaming the book`() {
        val msg = downloadUnavailableMessage("Bartleby", UnknownHostException("no such host"))
        assertTrue("must mention reaching the server, was: $msg", msg.contains("reach the server"))
    }

    @Test
    fun `a wrapped network failure is still read as a network failure`() {
        // Retrofit and okio wrap the real cause; matching only the top-level
        // type would report a genuine blip as a missing book.
        val msg = downloadUnavailableMessage("Bartleby", RuntimeException("wrapped", IOException("reset")))
        assertTrue("must mention reaching the server, was: $msg", msg.contains("reach the server"))
    }

    @Test
    fun `an expired session tells the user to sign in`() {
        val msg = downloadUnavailableMessage("Bartleby", RuntimeException("HTTP 401 Unauthorized"))
        assertTrue("must mention signing in, was: $msg", msg.contains("Sign in"))
    }

    @Test
    fun `a 404 reads as a missing book, not a broken app`() {
        val msg = downloadUnavailableMessage("Bartleby", RuntimeException("HTTP 404 Not Found"))
        assertTrue("must name the book, was: $msg", msg.contains("Bartleby"))
        assertTrue("must say it is gone from the server, was: $msg", msg.contains("server"))
    }

    @Test
    fun `anything unrecognised still carries the reason`() {
        val msg = downloadUnavailableMessage("Bartleby", IllegalStateException("boom"))
        assertTrue("must carry the underlying reason, was: $msg", msg.contains("boom"))
    }

    @Test
    fun `a missing reason never renders as null`() {
        val msg = downloadUnavailableMessage("Bartleby", IllegalStateException())
        assertTrue("must not leak a null into the UI, was: $msg", !msg.contains("null"))
    }

    // ------------------------------------------------------------------
    // Wiring. DownloadWorker itself needs a Context and a WorkManager to
    // instantiate, so there is no JVM test of `doWork`; this pins the one
    // line of it that issue #338 is about — the lookup that used to read
    // Room and give up. Same approach as DownloadNotificationIdTest.
    // ------------------------------------------------------------------

    private fun workerSource(): String {
        var dir = File("").absoluteFile
        val relative = "com/booksync/worker/DownloadWorker.kt"
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relative")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relative")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relative")
    }

    @Test
    fun `the worker resolves its target instead of reading Room and giving up`() {
        val worker = workerSource()
        for (resolver in listOf("resolveEbookById(", "resolveAudiobookById(", "resolvePairById(")) {
            assertTrue(
                "DownloadWorker must resolve through $resolver — a search result " +
                    "can name an id the local cache has never seen.",
                worker.contains(resolver),
            )
        }
        for (cacheOnly in listOf("getEbookById(", "getAudiobookById(", "getPairById(")) {
            assertTrue(
                "DownloadWorker still reads the cache directly via $cacheOnly, " +
                    "which fails on a cold cache.",
                !worker.contains("repository.$cacheOnly"),
            )
        }
        assertTrue(
            "the DB-shaped failure strings were never shown to anyone.",
            !worker.contains("not found in DB"),
        )
    }
}
