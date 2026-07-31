package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.resetPairProgress] backs "Reset Progress" for a paired
 * book. The legacy [BookSyncRepository.resetMediaProgress] only zero-writes
 * `user_progress` per media item and leaves the canonical Bookmark row in
 * place — the next sync (or even the next local open) re-seeds progress
 * right back from it, so the reset silently un-resets itself. This calls the
 * server's pair-scoped DELETE (which removes the Bookmark + hints + progress
 * rows) and clears the matching local caches — the Room bookmark row and any
 * queued pending_sync retries for the pair — so a stale local write can't
 * resurrect the old position or mis-route the next open.
 */
class ResetPairProgressTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = bookmarkDao,
        pendingSyncDao = pendingSyncDao,
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
    )

    @Test
    fun `issues the DELETE and removes the Room bookmark and pending_sync rows`() = runTest {
        coEvery { api.resetPairProgress(42) } returns Response.success(Unit)

        repository().resetPairProgress(42)

        coVerify(exactly = 1) { api.resetPairProgress(42) }
        coVerify(exactly = 1) { bookmarkDao.deleteBookmark(42) }
        coVerify(exactly = 1) { pendingSyncDao.deleteForPair(42) }
    }

    @Test
    fun `still clears local caches when the server call fails`() = runTest {
        coEvery { api.resetPairProgress(42) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        repository().resetPairProgress(42)

        coVerify(exactly = 1) { bookmarkDao.deleteBookmark(42) }
        coVerify(exactly = 1) { pendingSyncDao.deleteForPair(42) }
    }

    @Test
    fun `still clears local caches when the device is offline`() = runTest {
        coEvery { api.resetPairProgress(42) } throws java.io.IOException("offline")

        repository().resetPairProgress(42)  // must not throw

        coVerify(exactly = 1) { bookmarkDao.deleteBookmark(42) }
        coVerify(exactly = 1) { pendingSyncDao.deleteForPair(42) }
    }
}
