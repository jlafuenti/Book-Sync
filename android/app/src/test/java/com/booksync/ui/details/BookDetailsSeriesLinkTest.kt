package com.booksync.ui.details

import com.booksync.data.local.SeriesEntry
import com.booksync.data.local.encodeSeries
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.seriesIndexSuffix
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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

    private fun ebook(series: String?, index: Double? = 3.0) = EBookEntity(
        id = 7,
        title = "A Test Book",
        author = "An Author",
        filename = "a-test-book.epub",
        fileSize = 1_024L,
        format = "epub",
        seriesJson = encodeSeries(listOfNotNull(series?.let { SeriesEntry(it, index) })),
        uploadedAt = "2026-01-01T00:00:00Z",
        isDownloaded = false,
    )

    private fun pair(vararg series: SeriesEntry) = BookPairEntity(
        id = 9, ebookId = 7, ebookTitle = "A Test Book", ebookAuthor = "An Author",
        ebookFilename = "a.epub", ebookFormat = "epub", audiobookId = 8,
        audiobookTitle = "A Test Book", audiobookAuthor = "A Narrator",
        audiobookFilename = "a.m4b", audiobookFormat = "m4b", audiobookDurationSeconds = 3600,
        status = "synced", seriesJson = encodeSeries(series.toList()),
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
            extendedMeta = BookExtendedMeta(seriesList = listOf(SeriesEntry("Server Name", 2.0))),
        )

        assertEquals("Server Name", ui.seriesName)
        assertEquals("Server Name #2", ui.seriesLabel)
    }

    @Test
    fun `metadata with no series falls back to the cached row`() {
        val ui = BookDetailsUi(loading = false, ebook = ebook("Cached Name"), extendedMeta = BookExtendedMeta())

        assertEquals("Cached Name", ui.seriesName)
    }

    @Test
    fun `a blank series is no series, so nothing is offered to tap`() {
        assertNull(BookDetailsUi(loading = false, ebook = ebook("  ")).seriesName)
        assertNull(BookDetailsUi(loading = false, ebook = ebook(null)).seriesName)
        assertEquals(emptyList<SeriesEntry>(), BookDetailsUi(loading = false, ebook = ebook(null)).seriesList)
        assertNull(BookDetailsUi(loading = false, ebook = ebook(null)).seriesLabel)
    }

    // ---- several series (issue #908) ---------------------------------------

    private val saga = SeriesEntry("Axis Saga", 4.0)
    private val chrono = SeriesEntry("Axis Chronological", 2.5)

    @Test
    fun `every series is offered, primary first, and the label lists them all`() {
        val ui = BookDetailsUi(
            loading = false,
            ebook = ebook(null),
            extendedMeta = BookExtendedMeta(seriesList = listOf(saga, chrono)),
        )

        assertEquals(listOf(saga, chrono), ui.seriesList)
        assertEquals("Axis Saga", ui.seriesName)
        assertEquals("Axis Saga #4; Axis Chronological #2.5", ui.seriesLabel)
    }

    @Test
    fun `a pair also shows a series only its audiobook half has`() {
        // The metadata fetched for a pair is the ebook's; the pair row carries the union.
        val ui = BookDetailsUi(
            loading = false,
            pair = pair(saga, chrono),
            extendedMeta = BookExtendedMeta(seriesList = listOf(saga)),
        )

        assertEquals(listOf(saga, chrono), ui.seriesList)
    }

    @Test
    fun `a pair with no network metadata shows its stored union`() {
        val ui = BookDetailsUi(loading = false, pair = pair(saga, chrono))

        assertEquals(listOf(saga, chrono), ui.seriesList)
    }

    @Test
    fun `a number is only shown when the series has one`() {
        assertEquals(" #4", seriesIndexSuffix(saga))
        assertEquals("", seriesIndexSuffix(SeriesEntry("Axis Saga")))
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
                    "openSeriesInLibrary, or the name is tappable on some details pages " +
                    "and dead on others.",
                args.contains("onOpenSeries = openSeriesInLibrary"),
            )
        }
    }

    /**
     * Found on the emulator: the details screen sits in the outer nav graph, but
     * the Library route lives in the bottom-tab graph nested inside Main. Calling
     * the outer controller's navigate with the Library route threw "Navigation
     * destination ... cannot be found" and crashed the app. The tap must pop back
     * to Main and switch tabs on the bottom controller, the same shape as the
     * tour's GoToTab and Home's See all.
     */
    @Test
    fun `the series link switches tabs on the bottom controller, never the outer one`() {
        val nav = source("com/booksync/ui/BookSyncNavigation.kt")
        val helper = nav.substringAfter("val openSeriesInLibrary", "").substringBefore("\n    }")

        assertTrue("openSeriesInLibrary must exist", helper.isNotEmpty())
        assertTrue(
            "It must pop the details screen back to Main first.",
            helper.contains("navController.popBackStack(Routes.MAIN, inclusive = false)"),
        )
        assertTrue(
            "It must navigate the bottom-tab controller, which owns the Library route.",
            helper.contains("bottomNavController.navigate(Routes.library(series = name))"),
        )
        assertFalse(
            "The outer controller cannot reach the Library route; navigating it there crashes.",
            Regex("""(?<![A-Za-z])navController\.navigate\(Routes\.library\(""").containsMatchIn(nav),
        )
    }
}
