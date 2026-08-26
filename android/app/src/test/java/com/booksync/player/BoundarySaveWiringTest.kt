package com.booksync.player

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for issue #164's service half: boundary saves (pause,
 * STATE_ENDED, cast switch, controller disconnect, seek flush, onDestroy)
 * must run detached — on the repository's app scope under NonCancellable —
 * because the service cancels its own scope in onDestroy, and a boundary save
 * still inside the network call was cancelled before the Room row was
 * written. The throttled heartbeat deliberately stays on serviceScope: a lost
 * tick is recovered by the next one.
 */
class BoundarySaveWiringTest {

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
    fun `onDestroy flushes the position BEFORE cancelling the service scope`() {
        val lines = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        val destroyIdx = lines.indexOfFirst { it.startsWith("override fun onDestroy") }
        assertTrue("onDestroy not found", destroyIdx >= 0)
        val after = lines.drop(destroyIdx)
        val cancelIdx = after.indexOfFirst { it.contains("serviceScope.cancel()") }
        assertTrue("serviceScope.cancel() not found in onDestroy", cancelIdx >= 0)
        val flushIdx = after.take(cancelIdx).indexOfFirst {
            it.contains("saveCurrentPositionForAuto(")
        }
        assertTrue(
            "onDestroy must issue a final detached position flush on a code line " +
                "BEFORE serviceScope.cancel() — the SharedPreferences-only " +
                "saveLastPosition is a Cast hint, not a position save (issue #164).",
            flushIdx >= 0,
        )
    }

    @Test
    fun `every boundary save is detached and the heartbeat is not`() {
        val text = source("com/booksync/player/AudioPlayerService.kt")
        val detachedCount = Regex("detached = true").findAll(text).count()
        assertTrue(
            "Expected at least 6 detached boundary saves (pause, STATE_ENDED, cast " +
                "switch, seek flush, onDisconnected, onDestroy) — found $detachedCount.",
            detachedCount >= 6,
        )

        // The heartbeat call site passes the throttle verdict as pushToServer;
        // it must NOT be detached.
        val heartbeatSite = text.lineSequence()
            .dropWhile { !it.contains("pushToServer = heartbeatThrottle.shouldPush") }
            .firstOrNull()
        assertTrue("heartbeat call site not found", heartbeatSite != null)
        assertEquals(
            "the heartbeat stays on serviceScope — a lost tick is recovered by the next one",
            false,
            text.substringBefore("pushToServer = heartbeatThrottle.shouldPush")
                .substringAfterLast("saveCurrentPositionForAuto(")
                .contains("detached = true"),
        )
    }
}
