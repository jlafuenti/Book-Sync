package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for issue #684: tapping the playback notification's cover art
 * or body must open Tandem on the player screen. Media3 sends that tap
 * through the session's *session activity* — the PendingIntent it uses as
 * the notification's content intent — not through the transport button
 * actions, which are unaffected. [AudioPlayerService] is excluded from Kover
 * (Android service glue with no JVM-testable surface), so this pins the
 * wiring by reading the source, the same way [MediaIdWiringTest] and
 * [PauseOwnershipWiringTest] do for their own service-only invariants.
 */
class NotificationOpensPlayerWiringTest {

    private fun source(relativePath: String): String {
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

    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    @Test
    fun `the session is given a session activity when it is built`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "MediaLibrarySession.Builder must call setSessionActivity — without " +
                "one, Media3 has no content intent at all, and a tap on the " +
                "notification's cover art or body does nothing (issue #684).",
            service.any { it.contains(".setSessionActivity(") },
        )
    }

    @Test
    fun `the session activity is replaced on every media item transition`() {
        val service = source("com/booksync/player/AudioPlayerService.kt")
        val transition = service.substringAfter("override fun onMediaItemTransition(")
            .substringBefore("override fun onPositionDiscontinuity(")
        assertTrue(
            "onMediaItemTransition must call setSessionActivity with the new " +
                "item's PendingIntent — otherwise a tap on the notification keeps " +
                "opening whatever book was loaded first (or nothing), never the " +
                "book that is actually playing.",
            codeLines(transition).any { it.contains("setSessionActivity(") },
        )
    }

    @Test
    fun `the notification's PendingIntent targets MainActivity with the open-player action`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "The session-activity PendingIntent must launch MainActivity — " +
                "nothing else is the app's entry point.",
            service.any { it.contains("MainActivity::class.java") },
        )
        assertTrue(
            "The PendingIntent must carry ACTION_OPEN_PLAYER so MainActivity can " +
                "tell a notification tap apart from an ordinary launch.",
            service.any { it.contains("ACTION_OPEN_PLAYER") },
        )
    }

    @Test
    fun `the session activity PendingIntent is immutable and reuses one request code`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "The PendingIntent must be FLAG_IMMUTABLE (required on modern " +
                "Android for a PendingIntent handed to the system) combined with " +
                "FLAG_UPDATE_CURRENT (so replacing it on a transition updates the " +
                "existing one instead of leaking a new one).",
            service.any {
                it.contains("PendingIntent.FLAG_IMMUTABLE") && it.contains("PendingIntent.FLAG_UPDATE_CURRENT")
            },
        )
    }
}
