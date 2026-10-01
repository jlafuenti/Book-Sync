package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/** Source guard for read-along's wiring (issue #762); the logic is in [ReadAlongControllerTest]. */
class ReadAlongWiringTest {

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

    private val nav by lazy { source("com/booksync/ui/BookSyncNavigation.kt") }
    private val player by lazy { source("com/booksync/ui/player/PlayerScreen.kt") }
    private val screen by lazy { source("com/booksync/ui/reader/ReaderScreen.kt") }
    private val activity by lazy { source("com/booksync/ui/reader/ReaderActivity.kt") }

    @Test
    fun `the reader route carries a readAlong flag`() {
        assertTrue(nav.contains("readAlong={readAlong}"))
        assertTrue(nav.contains("navArgument(\"readAlong\") { type = NavType.BoolType; defaultValue = false }"))
    }

    @Test
    fun `the player's Read along entry navigates with the flag and does not pause`() {
        val block = player.substringBefore("\"Read along\"").substringAfterLast("IconButton(")
        assertTrue("Read along must not call stopAndSave — the audio keeps playing", !block.contains("stopAndSave"))
        assertTrue(block.contains("onReadAlong(positionMs)"))
        assertTrue(nav.contains("onReadAlong = { audioMs ->"))
        assertTrue(nav.contains("Routes.reader(pairId, audioMs, readAlong = true)"))
    }

    @Test
    fun `ReaderScreen forwards the flag as the activity extra`() {
        assertTrue(screen.contains("putExtra(ReaderActivity.EXTRA_READ_ALONG, true)"))
        assertTrue(activity.contains("const val EXTRA_READ_ALONG = \"readAlong\""))
    }
}
