package com.booksync.ui.library

import com.booksync.data.local.SeriesEntry
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Search results sort by series (issue #908: a book in several series sorts by
 * its primary one), and a result's label shows the primary series plus a count.
 */
class SearchSeriesTest {

    private fun result(id: String, title: String, vararg series: SeriesEntry) = SearchResultItem(
        id = id, title = title, author = null, seriesList = series.toList(),
        isEbook = true, isAudiobook = false,
    )

    @Test
    fun `results sort by primary series, then number, then title`() {
        val sorted = sortSearchResults(
            listOf(
                result("ebook_1", "Zeta", SeriesEntry("Beta Saga", 2.0)),
                result("ebook_2", "Alpha", SeriesEntry("Beta Saga", 1.0)),
                result("ebook_3", "Gamma", SeriesEntry("The Alpha Saga", 5.0)),
                result("ebook_4", "No Series"),
                result("ebook_5", "Beta Twin", SeriesEntry("Beta Saga", 1.0)),
            ),
        )

        // "The" is ignored for the order; the unseried result comes last.
        assertEquals(listOf("ebook_3", "ebook_2", "ebook_5", "ebook_1", "ebook_4"), sorted.map { it.id })
    }

    @Test
    fun `a book in two series sorts under its primary series only`() {
        val sorted = sortSearchResults(
            listOf(
                result("ebook_1", "Crossover", SeriesEntry("Zed Saga", 1.0), SeriesEntry("Aardvark Saga", 1.0)),
                result("ebook_2", "Plain", SeriesEntry("Mid Saga", 1.0)),
            ),
        )

        assertEquals(listOf("ebook_2", "ebook_1"), sorted.map { it.id })
    }
}
