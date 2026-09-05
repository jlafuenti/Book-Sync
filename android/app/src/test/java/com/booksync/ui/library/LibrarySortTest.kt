package com.booksync.ui.library

import com.booksync.R
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.LastOpenedTimes
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The library sort menu (issue #223).
 *
 * Two things were wrong. "Recently opened" was a copy of "Recently added" —
 * `compareByDescending { id }` with a comment promising that the UI re-ordered
 * by progress, which no screen ever did — so the menu named something it did
 * not do. And the labels drifted from the web's ("Recently added" vs "Date
 * added") while the two clients show the same library.
 *
 * The default stays [LibrarySort.RecentlyAdded]: the phone has always opened
 * newest-first, and changing it would move every existing user's library out
 * from under them for the sake of matching a browser they may never open.
 */
class LibrarySortTest {

    // --- fixtures ----------------------------------------------------------

    private fun ebook(id: Int, title: String, lastOpenedAt: Long? = null) =
        LibraryItem(
            key = "ebook_$id",
            ebook = EBookEntity(
                id = id, title = title, author = null, filename = "$id.epub",
                fileSize = null, format = "epub", series = null, seriesIndex = null,
                uploadedAt = "2026-01-01T00:00:00Z",
            ),
            lastOpenedAt = lastOpenedAt,
        )

    private fun pair(id: Int, ebookId: Int, audiobookId: Int) = BookPairEntity(
        id = id, ebookId = ebookId, ebookTitle = "Pair $id", ebookAuthor = null,
        ebookFilename = "$id.epub", ebookFormat = "epub",
        audiobookId = audiobookId, audiobookTitle = "Pair $id", audiobookAuthor = null,
        audiobookFilename = "$id.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = null, status = "synced",
    )

    private fun audiobook(id: Int) = AudioBookEntity(
        id = id, title = "Audio $id", author = null, filename = "$id.m4b",
        durationSeconds = null, format = "m4b", series = null, seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00Z",
    )

    // --- default -----------------------------------------------------------

    @Test
    fun `the default sort is date added, newest first`() {
        assertEquals(LibrarySort.RecentlyAdded, LibraryUiState().sort)
    }

    // --- Recently opened ---------------------------------------------------

    @Test
    fun `Recently opened orders by when the book was last opened, not by id`() {
        // The ids ascend in the opposite direction to the intended order, so the
        // id-descending comparator that shipped fails this outright.
        val items = listOf(
            ebook(1, "Oldest id, opened last week", lastOpenedAt = 1_000L),
            ebook(2, "Middle id, opened just now", lastOpenedAt = 9_000L),
            ebook(3, "Newest id, opened a year ago", lastOpenedAt = 10L),
        )

        val sorted = items.sortedWith(comparatorFor(LibrarySort.RecentlyOpened))

        assertEquals(listOf(2, 1, 3), sorted.map { it.ebook!!.id })
    }

    @Test
    fun `Recently opened puts never-opened books last, ordered by title`() {
        val items = listOf(
            ebook(1, "Zebra", lastOpenedAt = null),
            ebook(2, "Opened", lastOpenedAt = 500L),
            ebook(3, "Apple", lastOpenedAt = null),
        )

        val sorted = items.sortedWith(comparatorFor(LibrarySort.RecentlyOpened))

        assertEquals(listOf("Opened", "Apple", "Zebra"), sorted.map { it.title })
    }

    // --- Title -------------------------------------------------------------

    @Test
    fun `Title A-Z is case-insensitive, like the server's _browse_order`() {
        // The server lowercases in SQL (`server/routers/library.py::_browse_order`).
        // A case-sensitive comparator sorts every capitalised title ahead of every
        // lowercase one, so "aardvark" would land after "Zebra".
        val items = listOf(
            ebook(1, "Zebra"),
            ebook(2, "aardvark"),
            ebook(3, "Banana"),
        )

        val sorted = items.sortedWith(comparatorFor(LibrarySort.TitleAsc))

        assertEquals(listOf("aardvark", "Banana", "Zebra"), sorted.map { it.title })
    }

    // --- Labels ------------------------------------------------------------

    private fun stringsXml(): String {
        var dir = File("").absoluteFile
        repeat(4) {
            for (c in listOf(
                File(dir, "app/src/main/res/values/strings.xml"),
                File(dir, "src/main/res/values/strings.xml"),
            )) if (c.exists()) return c.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("strings.xml not found from ${File("").absolutePath}")
    }

    @Test
    fun `Recently added is labelled Date added, matching the web`() {
        assertEquals(R.string.library_sort_date_added, LibrarySort.RecentlyAdded.labelRes)
        assertTrue(
            "strings.xml must spell the label exactly as the web's " +
                "LIBRARY_SORT_LABELS does (issue #223).",
            stringsXml().contains(
                """<string name="library_sort_date_added">Date added</string>""",
            ),
        )
    }

    @Test
    fun `every sort option takes its label from a string resource`() {
        // Issue #229 is migrating user-facing text out of Kotlin literals screen
        // by screen; a hard-coded label here would quietly reopen that hole.
        for (sort in LibrarySort.entries) {
            assertTrue("${sort.name} has no label resource", sort.labelRes != 0)
        }
        assertTrue(
            stringsXml().contains(
                """<string name="library_sort_recently_opened">Recently opened</string>""",
            ),
        )
    }

    // --- Which options the menu offers -------------------------------------

    @Test
    fun `series-only sorts stay out of the flat library menu`() {
        assertEquals(
            listOf(
                LibrarySort.RecentlyAdded,
                LibrarySort.RecentlyOpened,
                LibrarySort.TitleAsc,
                LibrarySort.AuthorAsc,
            ),
            sortOptionsFor(LibraryUiState()),
        )
    }

    @Test
    fun `grouped series mode offers Most books, drilled-in offers Series order`() {
        val grouped = sortOptionsFor(LibraryUiState(groupBySeries = true))
        assertTrue(LibrarySort.SeriesCount in grouped)
        assertTrue(LibrarySort.SeriesOrder !in grouped)

        val drilled = sortOptionsFor(LibraryUiState(seriesFilter = "Fever"))
        assertTrue(LibrarySort.SeriesOrder in drilled)
        assertTrue(LibrarySort.SeriesCount !in drilled)
    }

    // --- Where "last opened" comes from ------------------------------------

    @Test
    fun `a pair's last-opened moment comes from its bookmark`() {
        // A pair's position is written to `bookmarks` (ISO-8601 strings keyed by
        // bookPairId); a standalone's goes to `user_progress` (epoch millis keyed
        // by mediaType/mediaId). Reading only one table leaves half the library
        // looking never-opened.
        val times = LastOpenedTimes(pairs = mapOf(7 to 4_000L))
        val item = LibraryItem("pair_7", pair = pair(id = 7, ebookId = 70, audiobookId = 71))

        assertEquals(4_000L, lastOpenedFor(item, times))
    }

    @Test
    fun `a pair also counts progress written against its ebook or audiobook`() {
        // The server projects a pair-scoped write onto both standalone rows and a
        // pull can populate them, so take whichever moment is newest.
        val times = LastOpenedTimes(
            pairs = mapOf(7 to 4_000L),
            ebooks = mapOf(70 to 9_000L),
            audiobooks = mapOf(71 to 1_000L),
        )
        val item = LibraryItem("pair_7", pair = pair(id = 7, ebookId = 70, audiobookId = 71))

        assertEquals(9_000L, lastOpenedFor(item, times))
    }

    @Test
    fun `a never-opened item has no last-opened moment`() {
        assertNull(lastOpenedFor(ebook(1, "Untouched"), LastOpenedTimes()))
        assertNull(
            lastOpenedFor(
                LibraryItem("audiobook_5", audiobook = audiobook(5)),
                LastOpenedTimes(audiobooks = mapOf(6 to 3_000L)),
            ),
        )
    }

    @Test
    fun `an unparseable timestamp is treated as never opened`() {
        // parseSyncTimestamp returns 0L for anything it cannot read. Zero is not a
        // real "opened at the epoch" moment and must not sort above a book that
        // carries no timestamp at all.
        assertNull(lastOpenedFor(ebook(1, "Bad timestamp"), LastOpenedTimes(ebooks = mapOf(1 to 0L))))
    }
}
