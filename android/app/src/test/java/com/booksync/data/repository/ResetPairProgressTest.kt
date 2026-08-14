package com.booksync.data.repository

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.resetPairProgress] backs "Reset Progress" for a paired
 * book. The legacy `resetMediaProgress` (removed in issue #103) only zero-wrote
 * `user_progress` per media item and leaves the canonical Bookmark row in
 * place — the next sync (or even the next local open) re-seeds progress
 * right back from it, so the reset silently un-resets itself. This calls the
 * server's pair-scoped DELETE (which removes the Bookmark + hints + progress
 * rows) and clears the matching local caches — the Room bookmark row, any
 * queued pending_sync retries, and the pair's own ebook/audiobook
 * `user_progress` rows — so a stale local write can't resurrect the old
 * position or mis-route the next open.
 *
 * Issue #61/#40 fix 3 tightened this further: local cleanup now only happens
 * once the server DELETE actually succeeds. Clearing local state on
 * failure/offline used to risk a stale, unsynced `user_progress` row being
 * picked up by `syncAllBookmarksAndProgress`/`processPendingSync` and pushed
 * back to the server — resurrecting exactly what the reset was supposed to
 * clear, on a server that was never actually told to reset.
 */
class ResetPairProgressTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)
    private val bookPairDao = mockk<BookPairDao>(relaxed = true)

    private fun pair(id: Int = 42) = BookPairEntity(
        id = id,
        ebookId = 100,
        ebookTitle = "Test Book",
        ebookAuthor = null,
        ebookFilename = "test.epub",
        ebookFormat = "epub",
        audiobookId = 200,
        audiobookTitle = "Test Book",
        audiobookAuthor = null,
        audiobookFilename = "test.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = "ready",
    )

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = bookPairDao,
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = bookmarkDao,
        pendingSyncDao = pendingSyncDao,
        userProgressDao = userProgressDao,
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
    )

    @Test
    fun `success issues the DELETE and clears bookmark, pending_sync, and user_progress rows`() = runTest {
        coEvery { api.resetPairProgress(42) } returns Response.success(Unit)
        coEvery { bookPairDao.getPairById(42) } returns pair()

        val result = repository().resetPairProgress(42)

        assertTrue("resetPairProgress must report success", result)
        coVerify(exactly = 1) { api.resetPairProgress(42) }
        coVerify(exactly = 1) { bookmarkDao.deleteBookmark(42) }
        coVerify(exactly = 1) { pendingSyncDao.deleteForPair(42) }
        coVerify(exactly = 1) { userProgressDao.deleteProgress("ebook", 100) }
        coVerify(exactly = 1) { userProgressDao.deleteProgress("audiobook", 200) }
    }

    @Test
    fun `HTTP failure clears nothing locally and returns false`() = runTest {
        coEvery { api.resetPairProgress(42) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        val result = repository().resetPairProgress(42)

        assertFalse("resetPairProgress must report failure", result)
        coVerify(exactly = 0) { bookmarkDao.deleteBookmark(any()) }
        coVerify(exactly = 0) { pendingSyncDao.deleteForPair(any()) }
        coVerify(exactly = 0) { userProgressDao.deleteProgress(any(), any()) }
    }

    @Test
    fun `offline clears nothing locally, does not throw, and returns false`() = runTest {
        coEvery { api.resetPairProgress(42) } throws java.io.IOException("offline")

        val result = repository().resetPairProgress(42)  // must not throw

        assertFalse("resetPairProgress must report failure when offline", result)
        coVerify(exactly = 0) { bookmarkDao.deleteBookmark(any()) }
        coVerify(exactly = 0) { pendingSyncDao.deleteForPair(any()) }
        coVerify(exactly = 0) { userProgressDao.deleteProgress(any(), any()) }
    }
}
