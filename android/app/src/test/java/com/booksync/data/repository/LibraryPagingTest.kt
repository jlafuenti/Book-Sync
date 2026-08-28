package com.booksync.data.repository

import com.booksync.data.local.dao.EBookDao
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.EBookResponse
import com.booksync.data.remote.PageResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The library list endpoints are paginated (issue #48). `refreshEbooks` /
 * `refreshAudiobooks` / `refreshPairs` mirror the server into Room and end with
 * `deleteOrphansExcept(remoteIds)`, so they must walk EVERY page before
 * touching Room — acting on one page would delete every book not on it.
 */
class LibraryPagingTest {

    private val api = mockk<BookSyncApi>()
    private val eBookDao = mockk<EBookDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
        eBookDao = eBookDao,
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = mockk(relaxed = true),
        pendingSyncDao = mockk(relaxed = true),
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun ebook(id: Int) = EBookResponse(
        id = id, title = "E$id", filename = "e$id.epub", format = "epub",
        uploaded_at = "2026-01-01T00:00:00Z",
    )

    @Test
    fun `fetchAllPages concatenates every page and stops on the short one`() = runTest {
        val pages = mapOf(
            1 to PageResponse(listOf(ebook(1), ebook(2)), total = 5, page = 1, limit = 2),
            2 to PageResponse(listOf(ebook(3), ebook(4)), total = 5, page = 2, limit = 2),
            3 to PageResponse(listOf(ebook(5)), total = 5, page = 3, limit = 2),
        )
        val requested = mutableListOf<Int>()

        val all = repository().fetchAllPages { page -> requested += page; pages.getValue(page) }

        assertEquals(listOf(1, 2, 3, 4, 5), all.map { it.id })
        assertEquals(listOf(1, 2, 3), requested)
    }

    @Test
    fun `fetchAllPages stops after one request when the first page is short or the total is met`() = runTest {
        val requested = mutableListOf<Int>()
        val all = repository().fetchAllPages { page ->
            requested += page
            PageResponse(listOf(ebook(1)), total = 1, page = page, limit = 500)
        }
        assertEquals(1, all.size)
        assertEquals(listOf(1), requested)

        requested.clear()
        val none = repository().fetchAllPages<EBookResponse> { page ->
            requested += page
            PageResponse(emptyList(), total = 0, page = page, limit = 500)
        }
        assertTrue(none.isEmpty())
        assertEquals(listOf(1), requested)
    }

    @Test
    fun `refreshEbooks upserts every page and only then prunes orphans against the full id set`() = runTest {
        coEvery { api.getEbooks(1, any()) } returns
            PageResponse(listOf(ebook(1), ebook(2)), total = 3, page = 1, limit = 2)
        coEvery { api.getEbooks(2, any()) } returns
            PageResponse(listOf(ebook(3)), total = 3, page = 2, limit = 2)
        // Make the server's page size 2 for this test by having the mock ignore
        // the requested limit — the repository trusts the `limit` it gets back.
        val upserted = slot<List<EBookEntity>>()
        coEvery { eBookDao.upsertEBooks(capture(upserted)) } returns Unit
        val kept = slot<List<Int>>()
        coEvery { eBookDao.deleteOrphansExcept(capture(kept)) } returns Unit

        repository().refreshEbooks()

        assertEquals(listOf(1, 2, 3), upserted.captured.map { it.id })
        assertEquals(listOf(1, 2, 3), kept.captured)
        coVerify(exactly = 0) { eBookDao.deleteAll() }
    }

    @Test
    fun `a failure mid-walk throws before anything is written to Room`() = runTest {
        coEvery { api.getEbooks(1, any()) } returns
            PageResponse(listOf(ebook(1), ebook(2)), total = 3, page = 1, limit = 2)
        coEvery { api.getEbooks(2, any()) } throws java.io.IOException("offline")

        try {
            repository().refreshEbooks()
            fail("expected the refresh to propagate the failure")
        } catch (_: java.io.IOException) {
        }

        coVerify(exactly = 0) { eBookDao.upsertEBooks(any()) }
        coVerify(exactly = 0) { eBookDao.deleteOrphansExcept(any()) }
        coVerify(exactly = 0) { eBookDao.deleteAll() }
    }
}
