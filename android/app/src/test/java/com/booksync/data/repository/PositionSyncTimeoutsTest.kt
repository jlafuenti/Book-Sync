package com.booksync.data.repository

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * One timeout for every bounded pull-before-resume (issue #167): the player,
 * the service and the reader all wait this long for the server's position
 * before falling back to the local cache. The constant used to be duplicated
 * per file — and the reader had none at all, so a captive-portal network could
 * stall a downloaded book's open for minutes.
 */
class PositionSyncTimeoutsTest {

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
    fun `the bounded-pull timeout is short enough not to visibly delay playback`() {
        assertEquals(1_500L, PositionSyncTimeouts.SERVER_POSITION_TIMEOUT_MS)
    }

    @Test
    fun `no surface keeps a private copy of the timeout`() {
        for (file in listOf(
            "com/booksync/ui/player/PlayerScreen.kt",
            "com/booksync/player/AudioPlayerService.kt",
        )) {
            assertTrue(
                "$file must use PositionSyncTimeouts.SERVER_POSITION_TIMEOUT_MS, not a " +
                    "private const copy — duplicated constants are how the reader " +
                    "ended up with no bound at all (issue #167).",
                codeLines(source(file)).none {
                    it.contains("const val SERVER_POSITION_TIMEOUT_MS")
                },
            )
        }
    }
}
