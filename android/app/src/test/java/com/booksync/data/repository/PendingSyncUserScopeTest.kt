package com.booksync.data.repository

import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.entity.PendingSyncEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.UserScopeProvider
import io.mockk.coEvery
import io.mockk.every
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Test
import retrofit2.Response

/**
 * Issue #314, the half that could corrupt data rather than merely disclose it.
 *
 * `pending_sync` is the offline write queue. `processPendingSync` read the whole
 * table with no owner filter and replayed each row through `api.updatePosition`,
 * which carries whatever token is currently stored — so one account's unsent
 * reading positions committed into another account the moment they signed in.
 * `SyncWorker` runs periodically and on every offline→online edge, so no user
 * action was required for it to fire.
 *
 * The queue is filtered rather than cleared on a user switch: clearing it would
 * silently destroy positions the server has never seen, which is the failure mode
 * this issue exists to avoid, not one to trade for.
 */
class PendingSyncUserScopeTest {

    private val api = mockk<BookSyncApi>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userScopeProvider = mockk<UserScopeProvider>(relaxed = true)

    private fun repository() = buildRepository(
        api = api,
        pendingSyncDao = pendingSyncDao,
        userScopeProvider = userScopeProvider,
    )

    private fun row(pairId: Int, scope: String) = PendingSyncEntity(
        id = pairId,
        scopeKey = scope,
        bookPairId = pairId,
        source = "ebook",
        epubChapter = 1,
        epubSentenceIndex = 2,
        audioPositionMs = null,
    )

    @Test
    fun `the drain asks only for this user's rows`() = runTest {
        every { userScopeProvider.currentKey } returns TEST_SCOPE
        coEvery { pendingSyncDao.getPendingForScope(TEST_SCOPE) } returns emptyList()

        repository().processPendingSync()

        coVerify(exactly = 1) { pendingSyncDao.getPendingForScope(TEST_SCOPE) }
    }

    @Test
    fun `rows owned by this user are replayed`() = runTest {
        every { userScopeProvider.currentKey } returns TEST_SCOPE
        coEvery { pendingSyncDao.getPendingForScope(TEST_SCOPE) } returns listOf(row(5, scope = TEST_SCOPE))
        coEvery { api.updatePosition(any(), any(), any()) } returns Response.success(null)

        repository().processPendingSync()

        coVerify(exactly = 1) { api.updatePosition(any(), 5, any()) }
    }

    @Test
    fun `nothing is replayed when the signed-in user cannot be determined`() = runTest {
        // An unreadable token must not fall back to draining everything — that is
        // exactly the misattribution this issue is about.
        every { userScopeProvider.currentKey } returns null

        repository().processPendingSync()

        coVerify(exactly = 0) { pendingSyncDao.getPendingForScope(any()) }
        coVerify(exactly = 0) { api.updatePosition(any(), any(), any()) }
    }

}
