package com.booksync.ui.library

import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The Library's series behaviour: grouped stacks, the drill-in filter, the
 * search box and "Series order". These lived inline in `LibraryViewModel`'s
 * flows and had no test, which is how a change to how a book is assigned to a
 * series could ship unnoticed (issue #908 adds several series per book).
 */
class LibrarySeriesTest {

    private fun ebook(id: Int, title: String, series: String?, index: Float? = null) = LibraryItem(
        key = "ebook_$id",
        ebook = EBookEntity(
            id = id, title = title, author = "An Author", filename = "$id.epub",
            fileSize = null, format = "epub", series = series, seriesIndex = index,
            uploadedAt = "2026-01-01T00:00:00Z",
        ),
    )

    private val items = listOf(
        ebook(1, "Axis Two", "Axis Saga", 2f),
        ebook(2, "Axis One", "Axis Saga", 1f),
        ebook(3, "Standalone", null),
        ebook(4, "Other One", "Another Series", 1f),
    )

    // --- stacks ------------------------------------------------------------

    @Test
    fun `stacks group items by series with the rest under No Series`() {
        val stacks = buildSeriesStacks(items, LibrarySort.TitleAsc)

        assertEquals(listOf("Another Series", "Axis Saga", "No Series"), stacks.map { it.seriesName })
        assertEquals(listOf(1, 2), stacks.first { it.seriesName == "Axis Saga" }.items.map { it.ebook!!.id })
        assertEquals(1, stacks.first { it.seriesName == "No Series" }.totalCount)
    }

    @Test
    fun `Most books orders stacks by size, largest first`() {
        val stacks = buildSeriesStacks(items, LibrarySort.SeriesCount)

        assertEquals("Axis Saga", stacks.first().seriesName)
    }

    // --- series drill-in filter --------------------------------------------

    @Test
    fun `the series filter matches the name ignoring case`() {
        assertEquals(listOf(1, 2), filterBySeries(items, "axis saga").map { it.ebook!!.id })
        assertEquals(emptyList<Int>(), filterBySeries(items, "Axis").map { it.ebook!!.id })
    }

    // --- Series order ------------------------------------------------------

    @Test
    fun `Series order sorts by the number inside the series, unnumbered last`() {
        val sorted = (items + ebook(5, "Unnumbered", "Axis Saga"))
            .let { filterBySeries(it, "Axis Saga") }
            .sortedWith(comparatorFor(LibrarySort.SeriesOrder, "Axis Saga"))

        assertEquals(listOf(2, 1, 5), sorted.map { it.ebook!!.id })
    }

    // --- search ------------------------------------------------------------

    @Test
    fun `search matches title, author or series`() {
        assertEquals(listOf(1, 2), items.filter { matchesSearch(it, "saga") }.map { it.ebook!!.id })
        assertEquals(listOf(3), items.filter { matchesSearch(it, "standalone") }.map { it.ebook!!.id })
        assertEquals(4, items.count { matchesSearch(it, "an author") })
    }
}
