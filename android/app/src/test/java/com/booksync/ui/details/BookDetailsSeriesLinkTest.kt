package com.booksync.ui.details

import com.booksync.data.local.entity.EBookEntity
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #717: the series name on the details page opens that series.
 *
 * The label ("Series Name #3") was plain text, so reaching the next book in a
 * series meant backing out to the Library and filtering by hand. The name is now
 * a link to the Library filtered to that series (`Routes.library(series = …)`,
 * which already existed); the "#3" suffix stays plain text.
 *
 * [BookDetailsUi.seriesName] is pure and asserted directly. The tap itself is
 * drawn inline in the `@Composable` (excluded from Kover), so the wiring is
 * pinned by source guards, the same approach as [BookDetailsGatingWiringTest].
 */
class BookDetailsSeriesLinkTest {

    private fun ebook(series: String?) = EBookEntity(
        id = 7,
        title = "A Test Book",
        author = "An Author",
        filename = "a-test-book.epub",
        fileSize = 1_024L,
        format = "epub",
        series = series,
        seriesIndex = 3f,
        uploadedAt = "2026-01-01T00:00:00Z",
        isDownloaded = false,
    )

    @Test
    fun `the series name is the raw name, not the label with its number`() {
        val ui = BookDetailsUi(loading = false, ebook = ebook("Axis Test Saga"))

        assertEquals("Axis Test Saga", ui.seriesName)
        assertEquals("Axis Test Saga #3", ui.seriesLabel)
    }

    @Test
    fun `network metadata wins over the cached row, as it does for the label`() {
        val ui = BookDetailsUi(
            loading = false,
            ebook = ebook("Cached Name"),
            extendedMeta = BookExtendedMeta(series = "Server Name"),
        )

        assertEquals("Server Name", ui.seriesName)
    }

    @Test
    fun `a blank series is no series, so nothing is offered to tap`() {
        assertNull(BookDetailsUi(loading = false, ebook = ebook("  ")).seriesName)
        assertNull(BookDetailsUi(loading = false, ebook = ebook(null)).seriesName)
    }

    // ---- wiring -------------------------------------------------------------

    private fun source(rel: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$rel")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$rel")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $rel from ${File("").absolutePath}")
    }

    @Test
    fun `the details screen hands the series name to its caller on tap`() {
        val screen = source("com/booksync/ui/details/BookDetailsScreen.kt")

        assertTrue(
            "BookDetailsScreen must take an onOpenSeries callback (issue #717).",
            screen.contains("onOpenSeries: (String) -> Unit"),
        )
        assertTrue(
            "The series row must call onOpenSeries with the raw series name, not the " +
                "\"#3\"-suffixed label, or the Library filter matches nothing.",
            screen.contains("onOpenSeries(name)"),
        )
    }

    @Test
    fun `every details destination opens the Library filtered to the series`() {
        val nav = source("com/booksync/ui/BookSyncNavigation.kt")
        val screens = nav.split("com.booksync.ui.details.BookDetailsScreen(").drop(1)

        assertTrue("expected the details screen to be routed", screens.isNotEmpty())
        screens.forEach { call ->
            val args = call.substringBefore("\n            )")
            assertTrue(
                "Each BookDetailsScreen call site must wire onOpenSeries to " +
                    "Routes.library(series = …), or the name is tappable on some " +
                    "details pages and dead on others.",
                args.contains("onOpenSeries = { name -> navController.navigate(Routes.library(series = name)) }"),
            )
        }
    }
}
