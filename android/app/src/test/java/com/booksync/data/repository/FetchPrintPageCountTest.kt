package com.booksync.data.repository

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.EBookResponse
import io.mockk.coEvery
import io.mockk.mockk
import java.io.IOException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Issue #730, task 9. `fetchPrintPageCount` is the data-plumbing half of the
 * Android print page indicator: it asks the server for one ebook's
 * `print_page_count` and never lets a network failure propagate, since the
 * caller (Task 12's `ReaderActivity` wiring) falls back to whatever is
 * already cached in `ReaderProgressPrefs` when this returns null.
 */
class FetchPrintPageCountTest {

    private val api = mockk<BookSyncApi>()

    private fun library() = buildLibraryRepository(api = api)

    private fun ebookResponse(id: Int, printPageCount: Int?) = EBookResponse(
        id = id, title = "Remote", author = "A", filename = "remote.epub",
        file_size = 100, format = "epub", series = "S", series_index = 1f,
        uploaded_at = "2026-01-01T00:00:00", print_page_count = printPageCount,
    )

    @Test
    fun `returns the server's print page count`() = runTest {
        coEvery { api.getEbook(42) } returns ebookResponse(42, printPageCount = 342)

        assertEquals(342, library().fetchPrintPageCount(42))
    }

    @Test
    fun `returns null when the server has no print page count for this ebook`() = runTest {
        coEvery { api.getEbook(42) } returns ebookResponse(42, printPageCount = null)

        assertNull(library().fetchPrintPageCount(42))
    }

    @Test
    fun `returns null rather than throwing when the request fails`() = runTest {
        coEvery { api.getEbook(42) } throws IOException("connection refused")

        assertNull(library().fetchPrintPageCount(42))
    }
}
