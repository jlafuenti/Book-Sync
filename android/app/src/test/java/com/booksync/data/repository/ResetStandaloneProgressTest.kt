package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.remote.BookSyncApi
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.resetStandaloneProgress] backs "Reset Progress" for a
 * standalone (unpaired) ebook/audiobook (issue #103). The legacy
 * `resetMediaProgress` zero-write only pinned the local `user_progress` row at
 * 0 and pushed that as a normal position write — the server's canonical
 * bookmark survived, `GET /position` kept answering 200-with-zeros instead of
 * 204, and the next sync re-seeded the old position right back.
 *
 * This calls the server's scoped `DELETE /api/sync/position/{scope}/{id}`
 * (a true reset: bookmark + hints + progress projection gone, GET returns
 * 204) and clears the local `user_progress` row. Same honesty contract as
 * [BookSyncRepository.resetPairProgress]: local cleanup only after the server
 * DELETE succeeds; on failure/offline nothing changes locally and the method
 * returns `false` so callers can tell the user the reset did not happen.
 * (Local `bookmarks` and `pending_sync` are pair-keyed — a standalone has no
 * rows there to clear.)
 */
class ResetStandaloneProgressTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)

    private fun repository() = buildRepository(
        api = api,
        bookmarkDao = bookmarkDao,
        pendingSyncDao = pendingSyncDao,
        userProgressDao = userProgressDao,
    )

    @Test
    fun `success issues the scoped DELETE and clears the local user_progress row`() = runTest {
        coEvery { api.resetPosition("ebook", 100) } returns Response.success(Unit)

        val result = repository().resetStandaloneProgress("ebook", 100)

        assertTrue("resetStandaloneProgress must report success", result)
        coVerify(exactly = 1) { api.resetPosition("ebook", 100) }
        coVerify(exactly = 1) { userProgressDao.deleteProgress(TEST_SCOPE, "ebook", 100) }
    }

    @Test
    fun `audiobook scope maps through to the DELETE path`() = runTest {
        coEvery { api.resetPosition("audiobook", 200) } returns Response.success(Unit)

        val result = repository().resetStandaloneProgress("audiobook", 200)

        assertTrue(result)
        coVerify(exactly = 1) { api.resetPosition("audiobook", 200) }
        coVerify(exactly = 1) { userProgressDao.deleteProgress(TEST_SCOPE, "audiobook", 200) }
    }

    @Test
    fun `HTTP failure clears nothing locally and returns false`() = runTest {
        coEvery { api.resetPosition("ebook", 100) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        val result = repository().resetStandaloneProgress("ebook", 100)

        assertFalse("resetStandaloneProgress must report failure", result)
        coVerify(exactly = 0) { userProgressDao.deleteProgress(any(), any(), any()) }
    }

    @Test
    fun `offline clears nothing locally, does not throw, and returns false`() = runTest {
        coEvery { api.resetPosition("ebook", 100) } throws java.io.IOException("offline")

        val result = repository().resetStandaloneProgress("ebook", 100)  // must not throw

        assertFalse("resetStandaloneProgress must report failure when offline", result)
        coVerify(exactly = 0) { userProgressDao.deleteProgress(any(), any(), any()) }
    }
}
