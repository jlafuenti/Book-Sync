package com.booksync.ui.library

import com.booksync.data.local.SeriesEntry
import com.booksync.data.local.encodeSeries
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The Library's series behaviour: grouped stacks, the drill-in filter, the
 * search box and "Series order". These lived inline in `LibraryViewModel`'s
 * flows and had no test, which is how a change to how a book is assigned to a
 * series could ship unnoticed. A book can now be in several series (issue #908):
 * it shows in every stack, matches the filter by any of them and orders by the
 * number it has in the one being browsed.
 */
class LibrarySeriesTest {

    private val saga = "Axis Saga"
    private val chrono = "Axis Chronological"

    private fun ebook(id: Int, title: String, vararg series: SeriesEntry) = LibraryItem(
        key = "ebook_$id",
        ebook = EBookEntity(
            id = id, title = title, author = "An Author", filename = "$id.epub",
            fileSize = null, format = "epub", seriesJson = encodeSeries(series.toList()),
            uploadedAt = "2026-01-01T00:00:00Z",
        ),
    )

    private val items = listOf(
        ebook(1, "Axis Two", SeriesEntry(saga, 2.0)),
        ebook(2, "Axis One", SeriesEntry(saga, 1.0)),
        ebook(3, "Standalone"),
        ebook(4, "Other One", SeriesEntry("Another Series", 1.0)),
    )

    /** In both series, 3rd of the saga and 1st of the chronology. */
    private val both = ebook(5, "Axis Crossover", SeriesEntry(saga, 3.0), SeriesEntry(chrono, 1.0))

    private fun ids(list: List<LibraryItem>) = list.map { it.ebook!!.id }

    // --- stacks ------------------------------------------------------------

    @Test
    fun `stacks group items by series with the rest under No Series`() {
        val stacks = buildSeriesStacks(items, LibrarySort.TitleAsc)

        assertEquals(listOf("Another Series", saga, "No Series"), stacks.map { it.seriesName })
        assertEquals(listOf(1, 2), ids(stacks.first { it.seriesName == saga }.items))
        assertEquals(1, stacks.first { it.seriesName == "No Series" }.totalCount)
    }

    @Test
    fun `Most books orders stacks by size, largest first`() {
        val stacks = buildSeriesStacks(items, LibrarySort.SeriesCount)

        assertEquals(saga, stacks.first().seriesName)
    }

    @Test
    fun `a book in two series appears in both stacks`() {
        val stacks = buildSeriesStacks(items + both, LibrarySort.TitleAsc)

        assertEquals(listOf(1, 2, 5), ids(stacks.first { it.seriesName == saga }.items))
        assertEquals(listOf(5), ids(stacks.first { it.seriesName == chrono }.items))
        assertEquals(listOf(3), ids(stacks.first { it.seriesName == "No Series" }.items))
    }

    @Test
    fun `a book is never in No Series when it is in any series`() {
        val stacks = buildSeriesStacks(listOf(both), LibrarySort.TitleAsc)

        assertEquals(listOf(chrono, saga), stacks.map { it.seriesName })
    }

    @Test
    fun `stack names are grouped ignoring case and outer spaces`() {
        val list = listOf(ebook(1, "A", SeriesEntry("axis saga", 1.0)), ebook(2, "B", SeriesEntry(" Axis Saga ", 2.0)))

        val stacks = buildSeriesStacks(list, LibrarySort.TitleAsc)

        assertEquals(1, stacks.size)
        assertEquals("axis saga", stacks.single().seriesName)
        assertEquals(listOf(1, 2), ids(stacks.single().items))
    }

    // --- series drill-in filter --------------------------------------------

    @Test
    fun `the series filter matches the name ignoring case`() {
        assertEquals(listOf(1, 2), ids(filterBySeries(items, "axis saga")))
        assertEquals(emptyList<Int>(), ids(filterBySeries(items, "Axis")))
    }

    @Test
    fun `the series filter matches a secondary series`() {
        assertEquals(listOf(5), ids(filterBySeries(items + both, chrono)))
        assertEquals(listOf(1, 2, 5), ids(filterBySeries(items + both, saga)))
    }

    // --- Series order ------------------------------------------------------

    @Test
    fun `Series order sorts by the number inside the series, unnumbered last`() {
        val unnumbered = ebook(6, "Unnumbered", SeriesEntry(saga))
        val sorted = filterBySeries(items + unnumbered, saga)
            .sortedWith(comparatorFor(LibrarySort.SeriesOrder, saga))

        assertEquals(listOf(2, 1, 6), ids(sorted))
    }

    @Test
    fun `Series order uses the number in the series being browsed, not the primary's`() {
        // 5 is #3 in the saga but #1 in the chronology; 6 is #2 in the chronology.
        val sixth = ebook(6, "Axis Prequel", SeriesEntry(chrono, 2.0))
        val inChrono = filterBySeries(items + both + sixth, chrono)

        assertEquals(listOf(5, 6), ids(inChrono.sortedWith(comparatorFor(LibrarySort.SeriesOrder, chrono))))
        // And in the saga the crossover comes last of the three.
        val inSaga = filterBySeries(items + both, saga)
        assertEquals(listOf(2, 1, 5), ids(inSaga.sortedWith(comparatorFor(LibrarySort.SeriesOrder, saga))))
    }

    @Test
    fun `Series order matches the series name ignoring case`() {
        val sorted = filterBySeries(items + both, "AXIS CHRONOLOGICAL")
            .sortedWith(comparatorFor(LibrarySort.SeriesOrder, "AXIS CHRONOLOGICAL"))

        assertEquals(listOf(5), ids(sorted))
    }

    // --- search ------------------------------------------------------------

    @Test
    fun `search matches title, author or series`() {
        assertEquals(listOf(1, 2), ids(items.filter { matchesSearch(it, "saga") }))
        assertEquals(listOf(3), ids(items.filter { matchesSearch(it, "standalone") }))
        assertEquals(4, items.count { matchesSearch(it, "an author") })
    }

    @Test
    fun `search matches a secondary series`() {
        assertEquals(listOf(5), ids((items + both).filter { matchesSearch(it, "chronolog") }))
    }

    // --- the item's own series ---------------------------------------------

    @Test
    fun `an item's series list is its entity's, primary first`() {
        assertEquals(listOf(SeriesEntry(saga, 3.0), SeriesEntry(chrono, 1.0)), both.seriesList)
        assertEquals(emptyList<SeriesEntry>(), items[2].seriesList)
    }

    @Test
    fun `a pair item reads the pair's stored union`() {
        val pair = BookPairEntity(
            id = 9, ebookId = 1, ebookTitle = "Axis Pair", ebookAuthor = null,
            ebookFilename = "p.epub", ebookFormat = "epub", audiobookId = 2,
            audiobookTitle = "Axis Pair", audiobookAuthor = null, audiobookFilename = "p.m4b",
            audiobookFormat = "m4b", audiobookDurationSeconds = null, status = "synced",
            seriesJson = encodeSeries(listOf(SeriesEntry(saga, 1.0), SeriesEntry(chrono, 4.0))),
        )
        val item = LibraryItem("pair_9", pair = pair)

        assertEquals(listOf(saga, chrono), item.seriesList.map { it.name })
        assertEquals(listOf(9), filterBySeries(listOf(item), chrono).map { it.pair!!.id })
    }

    @Test
    fun `a pair with no series does not fall through to anything`() {
        val pair = BookPairEntity(
            id = 9, ebookId = 1, ebookTitle = "Plain", ebookAuthor = null,
            ebookFilename = "p.epub", ebookFormat = "epub", audiobookId = 2,
            audiobookTitle = "Plain", audiobookAuthor = null, audiobookFilename = "p.m4b",
            audiobookFormat = "m4b", audiobookDurationSeconds = null, status = "synced",
        )

        assertNull(LibraryItem("pair_9", pair = pair).seriesList.firstOrNull())
    }
}
