package com.booksync.data.repository

import com.booksync.data.local.dao.NextUpDismissalDao
import com.booksync.data.local.entity.NextUpDismissalEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.NextUpDismissalDto
import com.booksync.data.remote.NextUpDismissalRequest
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.coVerifyOrder
import io.mockk.mockk
import io.mockk.slot
import java.io.IOException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * Issue #923: the phone's copy of the "removed from Next up" list.
 *
 * - dismiss and undo write the local row first, so the carousel reacts at once
 *   and offline still works, then call the server;
 * - a failed call is swallowed and the local row stands (no retry queue). The
 *   next successful refresh replaces the table with the server's list, so a
 *   dismissal made offline hides the row until then and no longer; that is the
 *   deliberate simplification, same as acknowledged items (#222);
 * - a failed fetch leaves the table as it is.
 */
class NextUpDismissalRepositoryTest {

    private val api = mockk<BookSyncApi>()
    private val dao = mockk<NextUpDismissalDao>(relaxed = true)

    private fun library() = buildLibraryRepository(api = api, nextUpDismissalDao = dao)

    @Test
    fun `refresh replaces the table with the server's list`() = runTest {
        coEvery { api.getNextUpDismissals() } returns listOf(
            NextUpDismissalDto(series = " Axis Saga ", dismissed_at = "2026-11-03T12:00:00"),
        )
        val rows = slot<List<NextUpDismissalEntity>>()
        coEvery { dao.replaceAll(capture(rows)) } returns Unit

        library().refreshNextUpDismissals()

        val row = rows.captured.single()
        assertEquals("axis saga", row.seriesKey)
        assertEquals(" Axis Saga ", row.series)
        assertEquals(1_793_707_200_000L, row.dismissedAtMs)
    }

    @Test
    fun `a failed fetch leaves the table alone`() = runTest {
        coEvery { api.getNextUpDismissals() } throws IOException("offline")

        library().refreshNextUpDismissals()

        coVerify(exactly = 0) { dao.replaceAll(any()) }
    }

    @Test
    fun `dismiss writes the local row then posts`() = runTest {
        coEvery { api.dismissNextUp(any()) } returns NextUpDismissalDto("Axis Saga", "2026-11-03T12:00:00")
        val row = slot<NextUpDismissalEntity>()
        coEvery { dao.upsert(capture(row)) } returns Unit

        library().dismissNextUp("  Axis Saga")

        assertEquals("axis saga", row.captured.seriesKey)
        assertTrue(row.captured.dismissedAtMs > 0L)
        coVerifyOrder {
            dao.upsert(any())
            api.dismissNextUp(NextUpDismissalRequest("  Axis Saga"))
        }
    }

    @Test
    fun `a failed post keeps the local row`() = runTest {
        coEvery { api.dismissNextUp(any()) } throws IOException("offline")

        library().dismissNextUp("Axis Saga")

        coVerify(exactly = 1) { dao.upsert(any()) }
        coVerify(exactly = 0) { dao.deleteByKey(any()) }
    }

    @Test
    fun `undo deletes the local row then calls the server`() = runTest {
        coEvery { api.undoDismissNextUp(any()) } returns Response.success(null)

        library().undoDismissNextUp("Axis Saga")

        coVerifyOrder {
            dao.deleteByKey("axis saga")
            api.undoDismissNextUp("Axis Saga")
        }
    }

    @Test
    fun `a failed delete call keeps the row gone locally`() = runTest {
        coEvery { api.undoDismissNextUp(any()) } throws IOException("offline")

        library().undoDismissNextUp("Axis Saga")

        coVerify(exactly = 1) { dao.deleteByKey("axis saga") }
    }
}
