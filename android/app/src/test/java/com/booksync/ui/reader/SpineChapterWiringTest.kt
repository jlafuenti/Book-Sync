package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for where [ReaderActivity] crosses between Readium's reading
 * order and the server's chapter numbering (issue #804). The mapping itself
 * is [SpineChapterMapTest]; the restore rungs are [ReaderRestoreExecutorTest].
 * The activity is excluded from coverage, so this pins that every boundary
 * goes through the map, once.
 */
class SpineChapterWiringTest {

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

    private val activity by lazy { source("com/booksync/ui/reader/ReaderActivity.kt") }

    private fun codeLines(): List<String> =
        activity.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    private fun body(signature: String): String =
        activity.substringAfter(signature).substringBefore("\n    private fun ").substringBefore("\n    fun ")

    @Test
    fun `read-along builds its locator from the map, not by indexing the reading order with a server chapter`() {
        val locator = body("private fun sentenceLocator(point: SyncPointEntity)")
        assertFalse(locator.contains("readingOrder.getOrNull(point.epubChapter)"))
        assertTrue(locator.contains("chapterNumbering.readingOrderIndexOf(point.epubChapter)"))
    }

    @Test
    fun `the restore plan is sized by the full spine the stored chapter counts`() {
        val initial = body("private suspend fun getInitialLocator(pub: Publication)")
        assertTrue(initial.contains("spineCount = chapterNumbering.spineSize"))
        assertFalse(initial.contains("spineCount = pub.readingOrder.size"))
    }

    @Test
    fun `the restore ladder sees the book's chapter numbering`() {
        val spine = activity.substringAfter("private val spineSource = object : SpineSource").substringBefore("\n    }")
        assertTrue(spine.contains("override val chapterNumbering: SpineChapterMap get() = this@ReaderActivity.chapterNumbering"))
    }

    @Test
    fun `a saved position carries the server's chapter`() {
        val save = body("private fun savePosition(locator: Locator)")
        assertTrue(save.contains("val serverChapter = pub.serverChapterOf(locator)"))
        assertTrue("the standalone save", save.contains("epubChapter = serverChapter,"))
        assertTrue("the paired snapshot", save.contains("chapterIndex = serverChapter,"))
    }

    @Test
    fun `every page-to-audio match and handoff is hinted in the server's chapter`() {
        val lines = codeLines()
        val matches = lines.filter { it.contains("repository.epubToAudioText(pairId, ") }
        assertEquals("the page, the follow start and the read-along selection", 3, matches.size)
        matches.forEach { assertTrue(it, it.contains("epubToAudioText(pairId, serverChapter,")) }

        val handoffs = lines.filter { it.startsWith("chapterIndex = ") }
        assertTrue(handoffs.isNotEmpty())
        handoffs.forEach { assertEquals(it, "chapterIndex = serverChapter,", it) }
    }

    @Test
    fun `nothing turns a reader position into a chapter without saying which numbering`() {
        val lines = codeLines()
        assertTrue(
            "spineIndexOf was the ambiguous name; use readingOrderIndexOf or serverChapterOf",
            lines.none { it.contains(".spineIndexOf(") },
        )
    }
}
