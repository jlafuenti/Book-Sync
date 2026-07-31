package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.PendingSyncEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PositionResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.saveReaderPosition] is the fix for issue #61/#40's
 * close-flush cancellation bug: the reader used to run the whole save body
 * (Room write, THEN the server PUT) inside `lifecycleScope.launch`, so a
 * back-press -> onPause -> finish() sequence cancelled that coroutine
 * mid-network-call and lost BOTH writes — the Room write happened after the
 * network call in the same cancellable coroutine.
 *
 * These tests pin the fix's three load-bearing properties: the Room write
 * happens first and unconditionally, cancelling the caller's coroutine can't
 * abort the save because it runs on the repository's own [BookSyncRepository.appScope],
 * and a canonical-write failure falls through to the legacy pending-sync
 * queue carrying the position's true capture time.
 */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class SaveReaderPositionTest {

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

    private fun snapshot(capturedAtMillis: Long = 1_700_000_000_000L) = ReaderPositionSnapshot(
        pairId = 42,
        chapterIndex = 12,
        epubSentenceIndex = 3,
        locatorJson = "{\"href\":\"ch12.xhtml\"}",
        textPreview = "It was a dark and stormy page.",
        progressPercent = 55.5f,
        audioPositionMs = 123_000,
        capturedAtMillis = capturedAtMillis,
    )

    private fun positionResponse() = PositionResponse(
        scope = "pair",
        book_pair_id = 42,
        source = "ebook",
        anchor_revision = 7L,
        updated_at = "2026-07-31T00:00:00Z",
    )

    @Test
    fun `Room write happens before the server call, regardless of the server result`() = runTest {
        val order = mutableListOf<String>()
        coEvery { bookmarkDao.upsertBookmark(any()) } answers { order.add("room") }
        coEvery { api.updatePosition(any(), any(), any()) } answers {
            order.add("server")
            Response.success(positionResponse())
        }

        val repo = repository()
        repo.saveReaderPosition(snapshot()).join()

        assertEquals(listOf("room", "server"), order)
    }

    @Test
    fun `cancelling the caller's job right after launch does not prevent the write`() = runTest {
        val gate = CompletableDeferred<Unit>()
        var serverCalled = false
        coEvery { api.updatePosition(any(), any(), any()) } coAnswers {
            gate.await()
            serverCalled = true
            Response.success(positionResponse())
        }
        var roomWritten = false
        coEvery { bookmarkDao.upsertBookmark(any()) } answers { roomWritten = true }

        val repo = repository()
        // Give the repository's app scope the same virtual clock as this test
        // so the assertions below are deterministic (no real threads/timeouts).
        repo.appScope = CoroutineScope(SupervisorJob() + StandardTestDispatcher(testScheduler))

        var saveJob: Job? = null
        val callerJob = launch {
            saveJob = repo.saveReaderPosition(snapshot())
        }
        runCurrent()
        callerJob.cancel() // simulates the activity's lifecycleScope being torn down

        assertFalse("save must not have completed while gated", roomWritten && serverCalled)
        gate.complete(Unit) // release the network call
        saveJob!!.join()

        assertTrue("Room write must survive caller cancellation", roomWritten)
        assertTrue("server call must survive caller cancellation", serverCalled)
    }

    @Test
    fun `server failure falls back to the legacy pending-sync queue with the true capture time`() = runTest {
        coEvery { api.updatePosition(any(), any(), any()) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        val pending = slot<PendingSyncEntity>()
        coEvery { pendingSyncDao.insert(capture(pending)) } returns Unit

        val capturedAt = 1_650_000_000_000L
        val repo = repository()
        repo.saveReaderPosition(snapshot(capturedAtMillis = capturedAt)).join()

        assertEquals(42, pending.captured.bookPairId)
        assertEquals(12, pending.captured.epubChapter)
        assertEquals(3, pending.captured.epubSentenceIndex)
        assertEquals(123_000, pending.captured.audioPositionMs)
        assertEquals("{\"href\":\"ch12.xhtml\"}", pending.captured.epubLocator)
        // The true moment the position was captured on-device — not whenever
        // this fallback happens to run — so a later replay isn't mistaken for
        // a fresh write (issue #54).
        assertEquals(capturedAt, pending.captured.createdAt)
    }

    @Test
    fun `server success does not enqueue a pending-sync fallback`() = runTest {
        coEvery { api.updatePosition(any(), any(), any()) } returns Response.success(positionResponse())

        repository().saveReaderPosition(snapshot()).join()

        coVerify(exactly = 0) { pendingSyncDao.insert(any()) }
    }
}
