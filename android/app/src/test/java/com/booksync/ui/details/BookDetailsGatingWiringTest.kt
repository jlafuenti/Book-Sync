package com.booksync.ui.details

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for the details screen's secondary actions (issue #484).
 *
 * `primaryAction` is pure and covered by [BookDetailsPrimaryActionTest], but the
 * rows beneath it are drawn inline in the `@Composable`, and Compose is excluded
 * from Kover. That is not incidental to this issue — it is *why* it existed:
 * "Refresh sync data", "Reset progress" and "Mark complete" were rendered
 * unconditionally for years, and no test in the repository could observe them.
 *
 * These assertions are deliberately shallow. They cannot prove the conditions
 * are right; they prove a condition is there at all, which is the thing that was
 * missing. Same approach as `PauseOwnershipWiringTest` and the source guards at
 * the bottom of `PairMenuActionsTest`.
 */
class BookDetailsGatingWiringTest {

    private fun screen(): String {
        var dir = File("").absoluteFile
        val rel = "com/booksync/ui/details/BookDetailsScreen.kt"
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$rel")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$rel")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $rel from ${File("").absolutePath}")
    }

    private fun rowFollows(source: String, title: String, guard: String): Boolean {
        // The `if (…) {` opening the block the ActionRow sits in is the line
        // before it in the file; search the 400 characters ahead of the guard.
        val at = source.indexOf(guard)
        if (at < 0) return false
        return source.substring(at, minOf(source.length, at + 400)).contains(title)
    }

    @Test
    fun `refresh sync data is gated on a cached map`() {
        val source = screen()
        assertTrue(
            "\"Refresh sync data\" re-downloads the cached sync map, so it must " +
                "check syncMapDownloaded — DownloadedScreen always did, and this " +
                "screen offering it anyway is why the same pair disagreed with itself.",
            rowFollows(source, "Refresh sync data", "if (pair.syncMapDownloaded)"),
        )
    }

    @Test
    fun `reset progress is gated on there being progress`() {
        val source = screen()
        assertTrue(
            "\"Reset progress\" was offered on books that had never been opened.",
            rowFollows(source, "Reset progress", "if (ui.progress.hasProgress)"),
        )
    }

    @Test
    fun `mark complete is gated on the book being unfinished`() {
        val source = screen()
        assertTrue(
            "\"Mark complete\" was offered on books already complete.",
            rowFollows(source, "Mark complete", "if (!ui.progress.isComplete)"),
        )
    }

    /**
     * The gap that made this screen the odd one out: it held the only "open the
     * book" button in the app that could not reach the player, so an
     * undownloaded pair had no way to start from the page built for starting it.
     */
    @Test
    fun `the screen can reach the player for a pair that is not downloaded`() {
        val source = screen()
        assertTrue(
            "primaryAction must consult isOnline — without it the nothing-" +
                "downloaded case falls to DownloadPair and the player stays " +
                "unreachable from this screen.",
            source.contains("ui.pair != null && ui.isOnline"),
        )
    }

    /**
     * Promoting Listen to the primary slot moves the download off it, so the
     * download has to reappear below or it is lost from this screen entirely.
     */
    @Test
    fun `the download survives losing the primary slot`() {
        val source = screen()
        assertTrue(
            "a \"Download pair\" row must exist for the case where Listen took " +
                "the primary button",
            source.contains("title = \"Download pair\""),
        )
    }
}
