package com.booksync.ui.downloaded

import com.booksync.data.local.entity.EBookEntity
import com.booksync.ui.library.LibrarySort
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The Downloaded tab's copy of the library sort (issue #223).
 *
 * It shows the same books from the same menu with the same [LibrarySort] enum,
 * and it carried the same `compareByDescending { sortId }` stand-in for
 * "Recently opened". Fixing only the library would have left the identical lie
 * one tab away.
 *
 * What is deliberately *not* shared is title order: this tab strips a leading
 * article ("The Hobbit" files under H) and the library does not.
 */
class DownloadedSortTest {

    private val articleRegex = "^(the|a|an)\\s+".toRegex(RegexOption.IGNORE_CASE)

    private fun ebook(id: Int, title: String, lastOpenedAt: Long? = null) = DownloadedItem(
        ebook = EBookEntity(
            id = id, title = title, author = null, filename = "$id.epub",
            fileSize = null, format = "epub", series = null, seriesIndex = null,
            uploadedAt = "2026-01-01T00:00:00Z", isDownloaded = true,
        ),
        lastOpenedAt = lastOpenedAt,
    )

    @Test
    fun `Recently opened orders by last-opened time, not by id`() {
        val items = listOf(
            ebook(1, "Opened last week", lastOpenedAt = 1_000L),
            ebook(2, "Opened just now", lastOpenedAt = 9_000L),
            ebook(3, "Opened a year ago", lastOpenedAt = 10L),
        )

        val sorted = items.sortedWith(downloadedComparatorFor(LibrarySort.RecentlyOpened, articleRegex))

        assertEquals(listOf(2, 1, 3), sorted.map { it.ebook!!.id })
    }

    @Test
    fun `never-opened downloads sort last`() {
        val items = listOf(
            ebook(1, "Never opened"),
            ebook(2, "Opened", lastOpenedAt = 5L),
        )

        val sorted = items.sortedWith(downloadedComparatorFor(LibrarySort.RecentlyOpened, articleRegex))

        assertEquals(listOf("Opened", "Never opened"), sorted.map { it.title })
    }

    @Test
    fun `title order still ignores a leading article`() {
        val items = listOf(ebook(1, "The Hobbit"), ebook(2, "Ithaca"), ebook(3, "A Wizard of Earthsea"))

        val sorted = items.sortedWith(downloadedComparatorFor(LibrarySort.TitleAsc, articleRegex))

        assertEquals(listOf("The Hobbit", "Ithaca", "A Wizard of Earthsea"), sorted.map { it.title })
    }
}
