package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PositionResponse
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * The detached playback-save helpers (issue #164), mirror of
 * [SaveReaderPositionTest]'s cancellation test: a *boundary* save (pause,
 * STATE_ENDED, cast switch, controller disconnect, service onDestroy,
 * PlayerViewModel.onCleared) must survive its caller's scope being torn down.
 * The service used to launch these on its own serviceScope and then cancel
 * that scope in onDestroy — a save still inside the network call lost the
 * Room write too.
 *
 * The helpers run on [BookSyncRepository.appScope] under NonCancellable, so
 * cancelling the caller's job cannot abort the save mid-write.
 */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class SavePlaybackPositionDetachedTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
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
        userScopeProvider = testScopeProvider(),
    )

    private fun positionResponse() = PositionResponse(
        scope = "pair",
        book_pair_id = 42,
        audiobook_id = null,
        source = "audiobook",
        anchor_revision = 7L,
        updated_at = "2026-08-24T00:00:00Z",
    )

    @Test
    fun `cancelling the caller right after a detached paired save does not prevent either write`() = runTest {
        val gate = CompletableDeferred<Unit>()
        var serverCalled = false
        coEvery { api.updatePosition(any(), any(), any()) } coAnswers {
            gate.await()
            serverCalled = true
            Response.success(positionResponse())
        }
        var roomWritten = false
        coEvery { bookmarkDao.upsertBookmark(any()) } answers { roomWritten = true }
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null

        val repo = repository()
        repo.appScope = CoroutineScope(SupervisorJob() + StandardTestDispatcher(testScheduler))

        var saveJob: Job? = null
        val callerJob = launch {
            saveJob = repo.savePlaybackPositionDetached(
                pairId = 42, audioPositionMs = 5_000, appendToLog = true, claimFormat = false)
        }
        runCurrent()
        callerJob.cancel() // simulates AudioPlayerService.onDestroy / ViewModel clear

        gate.complete(Unit)
        saveJob!!.join()

        assertTrue("the Room write must have landed", roomWritten)
        assertTrue("the server call must have completed despite the cancel", serverCalled)
    }

    @Test
    fun `cancelling the caller right after a detached standalone save does not prevent either write`() = runTest {
        val gate = CompletableDeferred<Unit>()
        var serverCalled = false
        coEvery { api.updatePosition(any(), any(), any()) } coAnswers {
            gate.await()
            serverCalled = true
            Response.success(positionResponse())
        }
        var roomWritten = false
        coEvery { userProgressDao.upsertProgress(any()) } answers { roomWritten = true }

        val repo = repository()
        repo.appScope = CoroutineScope(SupervisorJob() + StandardTestDispatcher(testScheduler))

        var saveJob: Job? = null
        val callerJob = launch {
            saveJob = repo.savePlaybackPositionStandaloneDetached(
                audiobookId = 7, audioPositionMs = 3_000, claimFormat = false)
        }
        runCurrent()
        callerJob.cancel()

        gate.complete(Unit)
        saveJob!!.join()

        assertTrue("the Room write must have landed", roomWritten)
        assertTrue("the server call must have completed despite the cancel", serverCalled)
    }
}
