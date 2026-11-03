package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `AudioPlayerService` is excluded from Kover, so nothing executes the glue
 * that feeds [CastFetchWatchdog] (issue #932). This pins that each input is
 * still wired and that the outcome still ends the session through the Cast
 * framework (whose `onSessionEnded` already switches back to the phone), which
 * is the path that must not be duplicated.
 */
class CastFailFastWiringTest {

    private fun serviceSource(): String {
        var dir = File("").absoluteFile
        repeat(5) {
            for (rel in listOf(
                "app/src/main/java/com/booksync/player/AudioPlayerService.kt",
                "src/main/java/com/booksync/player/AudioPlayerService.kt",
            )) {
                val f = File(dir, rel)
                if (f.exists()) return f.readText()
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("AudioPlayerService.kt not found from ${File("").absolutePath}")
    }

    private fun assertWired(what: String, fragment: String) {
        assertTrue("AudioPlayerService no longer $what (missing `$fragment`)", serviceSource().contains(fragment))
    }

    @Test
    fun `the LOAD acknowledgement starts the window`() =
        assertWired("starts the fetch window on the LOAD ack", "onCastLoadAcked()")

    @Test
    fun `a token-matched request reaches the watchdog`() =
        assertWired("forwards the server's token-matched requests", "castWatchdog.onReceiverRequest(")

    @Test
    fun `both receiver error signals reach the watchdog`() {
        val source = serviceSource()
        assertTrue(
            "the IDLE/ERROR status check is not wired into onStatusUpdated",
            source.contains("isCastReceiverError(status.playerState, status.idleReason)"),
        )
        // onStatusUpdated's call plus onMediaError's.
        assertTrue(
            "onMediaError must report a receiver error",
            Regex("""onCastReceiverError\(\)""").findAll(source).count() >= 2,
        )
    }

    @Test
    fun `a bad verdict publishes the failure and then ends the session through the Cast framework`() {
        val source = serviceSource()
        val report = source.indexOf("castSessionMonitor.report(")
        val end = source.indexOf("endCurrentSession(true)")
        assertTrue("the failure is never published", report >= 0)
        assertTrue("the Cast session is never ended", end >= 0)
        assertTrue("publish the failure before ending the session", report < end)
    }

    @Test
    fun `the cast server's port comes from the setting`() =
        assertWired("reads the cast port setting", "preferredPort = { castPortManager.currentPort }")

    @Test
    fun `the server address is published for the Account screen`() {
        assertWired("publishes the server address", "castSessionMonitor.setServerAddress(castServer.start())")
        assertWired("clears the server address", "castSessionMonitor.setServerAddress(null)")
    }
}
