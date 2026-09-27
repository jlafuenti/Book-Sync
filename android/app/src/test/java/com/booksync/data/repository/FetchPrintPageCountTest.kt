package com.booksync.data.repository

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.EBookResponse
import io.mockk.coEvery
import io.mockk.mockk
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #730, task 9. `fetchPrintPageCount` is the data-plumbing half of the
 * Android print page indicator: it asks the server for one ebook's
 * `print_page_count` and never lets a network failure propagate. It tells a
 * count the server does not have (success with null: the reader clears its
 * stored count) apart from a request that failed (failure: the reader keeps
 * the count stored in `ReaderProgressPrefs`).
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
    fun `a success carries the server's print page count`() = runTest {
        coEvery { api.getEbook(42) } returns ebookResponse(42, printPageCount = 342)

        assertEquals(Result.success(342), library().fetchPrintPageCount(42))
    }

    @Test
    fun `a success with null says the server has no print page count, so the stored one clears`() = runTest {
        coEvery { api.getEbook(42) } returns ebookResponse(42, printPageCount = null)

        val result = library().fetchPrintPageCount(42)

        assertTrue(result.isSuccess)
        assertNull(result.getOrThrow())
    }

    @Test
    fun `a failed request is a failure, not a missing count, so the stored one stands`() = runTest {
        coEvery { api.getEbook(42) } throws IOException("connection refused")

        assertTrue(library().fetchPrintPageCount(42).isFailure)
    }

    @Test
    fun `cancellation is not swallowed as a failure`() = runTest {
        coEvery { api.getEbook(42) } throws CancellationException("left the reader")

        assertThrows(CancellationException::class.java) {
            kotlinx.coroutines.runBlocking { library().fetchPrintPageCount(42) }
        }
    }

    @Test
    fun `the repository facade the reader uses delegates to the library`() = runTest {
        coEvery { api.getEbook(42) } returns ebookResponse(42, printPageCount = 342)

        assertEquals(Result.success(342), buildRepository(api = api).fetchPrintPageCount(42))
    }
}
