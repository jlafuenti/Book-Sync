package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.BookmarkLogDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PositionResponse
import com.booksync.data.remote.PositionUpdateRequest
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * Product decision behind the `claimFormat` parameter on
 * [BookSyncRepository.savePlaybackPosition] / [BookSyncRepository.savePlaybackPositionStandalone]:
 * the format (`source` — "ebook" vs "audiobook", see [BookSyncRepository.resolvePairOpenTarget])
 * only follows actual consumption. A save claims the format when the player is
 * actively playing at save time, or the save was triggered by an explicit user
 * playback command (play/pause tap, seek, skip, sleep-timer stop, Android Auto
 * MediaSession commands). A save from a paused/idle player — heartbeat after
 * pause, teardown while paused, post-session refresh — must still persist the
 * position but must NOT claim the format: on the wire that means omitting
 * `source` entirely (the server keeps its stored value on omission — see
 * server `PositionUpdate`), and locally it means the Room bookmark's `source`
 * is left exactly as it was.
 *
 * This is the fix for the production bug where a backgrounded player's
 * teardown/heartbeat saves stamped `source = audiobook` and hijacked routing
 * back to the player after the user had switched to reading.
 */
class SavePlaybackPositionTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)
    private val bookmarkLogDao = mockk<BookmarkLogDao>(relaxed = true)

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
        bookmarkLogDao = bookmarkLogDao,
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun positionResponse(
        scope: String = "pair",
        pairId: Int? = 42,
        audiobookId: Int? = null,
        source: String = "audiobook",
    ) = PositionResponse(
        scope = scope,
        book_pair_id = pairId,
        audiobook_id = audiobookId,
        source = source,
        anchor_revision = 7L,
        updated_at = "2026-07-31T00:00:00Z",
    )

    // ============ savePlaybackPosition (paired) ============

    @Test
    fun `claimFormat=true sends source=audiobook on the wire and stamps Room`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns
            Response.success(positionResponse())
        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000, claimFormat = true)

        assertEquals("audiobook", sentRequest.captured.source)
        assertEquals("audiobook", savedBookmark.captured.source)
        assertEquals(5_000, savedBookmark.captured.audioPositionMs)
    }

    @Test
    fun `claimFormat=false omits source from the wire payload and leaves Room source untouched`() = runTest {
        val existing = BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 42, source = "ebook", epubChapter = 5, epubSentenceIndex = 1,
            audioPositionMs = 1_000, updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns existing
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns
            Response.success(positionResponse(source = "ebook"))
        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000, claimFormat = false)

        assertNull("source must be omitted from the wire payload", sentRequest.captured.source)
        assertEquals("ebook", savedBookmark.captured.source)
        // The position itself still advances even though the format doesn't claim.
        assertEquals(5_000, savedBookmark.captured.audioPositionMs)
    }

    @Test
    fun `claimFormat=false with NO existing local row escalates to a claim (issue 61-40 fix 4)`() = runTest {
        // A first-ever write has no stored `source` to preserve. Omitting it
        // here would leave the server's new-row default ("ebook") standing
        // while this device's local fallback below stamps "audiobook" —
        // permanent routing disagreement from write #1. The user did just
        // play this book, so this one write escalates to a claim.
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns
            Response.success(positionResponse())
        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000, claimFormat = false)

        assertEquals("audiobook", sentRequest.captured.source)
        assertEquals("audiobook", savedBookmark.captured.source)
    }

    @Test
    fun `claimFormat=false does not stamp source on the offline retry path either`() = runTest {
        val existing = BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 42, source = "ebook", epubChapter = 5, epubSentenceIndex = 1,
            audioPositionMs = 1_000, updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns existing
        coEvery { api.updatePosition("pair", 42, any()) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000, claimFormat = false)

        assertEquals("ebook", savedBookmark.captured.source)
    }

    // ============ one boundary save == one server write (issue #226) ============
    //
    // AudioPlayerService is now the sole owner of the pause write; the player
    // screen's poll loop and stopAndSave no longer save at all. That only helps
    // if a single call here really is a single write, so pin it: one PUT
    // carrying source=audiobook and append_to_log=true, and — when the push
    // lands — no local bookmark_log row (the server owns the history entry; the
    // local mirror is the offline path only).

    @Test
    fun `one boundary save is exactly one server write with source and append_to_log`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns
            Response.success(positionResponse())

        repository().savePlaybackPosition(
            pairId = 42, audioPositionMs = 5_000, appendToLog = true, claimFormat = true)

        coVerify(exactly = 1) { api.updatePosition("pair", 42, any()) }
        assertEquals("audiobook", sentRequest.captured.source)
        assertTrue(
            "a pause is a session boundary — the server writes the history row",
            sentRequest.captured.append_to_log,
        )
        assertEquals(5_000, sentRequest.captured.audio_position_ms)
        coVerify(exactly = 0) { bookmarkLogDao.insertLocal(any()) }
    }

    @Test
    fun `only a failed boundary push mirrors the history entry locally`() = runTest {
        // The counterpart to the test above: offline, the local mirror is the
        // only record, so it must still be written exactly once per save.
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        coEvery { api.updatePosition("pair", 42, any()) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        repository().savePlaybackPosition(
            pairId = 42, audioPositionMs = 5_000, appendToLog = true, claimFormat = true)

        coVerify(exactly = 1) { bookmarkLogDao.insertLocal(any()) }
    }

    // ============ savePlaybackPositionStandalone ============

    @Test
    fun `standalone claimFormat=true sends source=audiobook on the wire`() = runTest {
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("audiobook", 7, capture(sentRequest)) } returns
            Response.success(positionResponse(scope = "audiobook", pairId = null, audiobookId = 7))

        repository().savePlaybackPositionStandalone(
            audiobookId = 7, audioPositionMs = 3_000, claimFormat = true)

        assertEquals("audiobook", sentRequest.captured.source)
    }

    @Test
    fun `standalone claimFormat=false omits source from the wire payload`() = runTest {
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("audiobook", 7, capture(sentRequest)) } returns
            Response.success(positionResponse(scope = "audiobook", pairId = null, audiobookId = 7, source = "audiobook"))

        repository().savePlaybackPositionStandalone(
            audiobookId = 7, audioPositionMs = 3_000, claimFormat = false)

        assertNull(sentRequest.captured.source)
    }

    @Test
    fun `savePlaybackPosition defaults claimFormat to true (backward compatible)`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns
            Response.success(positionResponse())

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000)

        assertEquals("audiobook", sentRequest.captured.source)
    }

    // ============ pushToServer — the throttled heartbeat (issue #65) ============
    //
    // The service's 5s heartbeat writes Room on every tick but only pushes to
    // the server every 30s. A local-only tick must leave the row UNSYNCED so
    // the backstops (startup reconcile, the WorkManager sweep) still deliver
    // it if the app dies before the next push.

    @Test
    fun `pushToServer=false writes Room only, unsynced, and reports no push`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        val pushed = repository().savePlaybackPosition(
            pairId = 42, audioPositionMs = 5_000, claimFormat = true, pushToServer = false)

        assertFalse(pushed)
        coVerify(exactly = 0) { api.updatePosition(any(), any(), any()) }
        assertEquals(5_000, savedBookmark.captured.audioPositionMs)
        assertFalse("a throttled heartbeat has not reached the server", savedBookmark.captured.syncedToServer)
        coVerify(exactly = 0) { pendingSyncDao.insert(any()) }
    }

    @Test
    fun `pushToServer=true reports whether the canonical write landed`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        coEvery { api.updatePosition("pair", 42, any()) } returns Response.success(positionResponse())
        assertTrue(repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000))

        coEvery { api.updatePosition("pair", 42, any()) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))
        assertFalse(repository().savePlaybackPosition(pairId = 42, audioPositionMs = 6_000))
    }

    @Test
    fun `standalone pushToServer=false writes progress locally, unsynced, without a network call`() = runTest {
        val savedProgress = slot<UserProgressEntity>()
        coEvery { userProgressDao.upsertProgress(capture(savedProgress)) } returns Unit

        val pushed = repository().savePlaybackPositionStandalone(
            audiobookId = 7, audioPositionMs = 3_000, pushToServer = false)

        assertFalse(pushed)
        coVerify(exactly = 0) { api.updatePosition(any(), any(), any()) }
        assertEquals(3_000, savedProgress.captured.audioPositionMs)
        assertFalse(savedProgress.captured.syncedToServer)
    }

    @Test
    fun `processPendingSync pushes a bookmark row a throttled heartbeat left unsynced`() = runTest {
        // The 15-minute WorkManager sweep is the backstop the throttle relies
        // on when the app is killed between pushes; it must see these rows.
        val stale = BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 42, source = "audiobook", epubChapter = null, epubSentenceIndex = null,
            audioPositionMs = 9_000, updatedAt = "1000", capturedAt = "2026-08-15T10:00:00Z",
            syncedToServer = false,
        )
        coEvery { pendingSyncDao.getPendingForScope(TEST_SCOPE) } returns emptyList()
        coEvery { userProgressDao.getUnsyncedProgress(TEST_SCOPE) } returns emptyList()
        coEvery { bookmarkDao.getUnsyncedBookmarks(TEST_SCOPE) } returns listOf(stale)
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns Response.success(positionResponse())
        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        repository().processPendingSync()

        assertEquals(9_000, sentRequest.captured.audio_position_ms)
        assertEquals("2026-08-15T10:00:00Z", sentRequest.captured.captured_at)
        assertTrue(savedBookmark.captured.syncedToServer)
    }

    // ============ Room-first ordering (issue #164) ============
    //
    // Contract § "The write gate": "Saves must survive teardown … the local
    // write *before* the server call." The audio path used to run the PUT
    // first, so a boundary save cancelled mid-network-call (paused swipe-away,
    // offline pause burning the connect timeout) lost the Room row too — no
    // local position, and nothing for the reconcile/sweep to deliver.

    @Test
    fun `paired save writes the Room row before the server call`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        coEvery { api.updatePosition(any(), any(), any()) } coAnswers { awaitCancellation() }
        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        val saveJob = launch {
            repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000)
        }
        runCurrent() // the save is now parked inside the (hanging) PUT

        assertTrue("Room row must be written before the PUT", savedBookmark.isCaptured)
        assertFalse(
            "the pre-PUT row must be unsynced — the server has not seen it",
            savedBookmark.captured.syncedToServer,
        )
        saveJob.cancel()
    }

    @Test
    fun `standalone save writes the progress row before the server call`() = runTest {
        coEvery { api.updatePosition(any(), any(), any()) } coAnswers { awaitCancellation() }
        val savedProgress = slot<UserProgressEntity>()
        coEvery { userProgressDao.upsertProgress(capture(savedProgress)) } returns Unit

        val saveJob = launch {
            repository().savePlaybackPositionStandalone(audiobookId = 7, audioPositionMs = 3_000)
        }
        runCurrent()

        assertTrue("progress row must be written before the PUT", savedProgress.isCaptured)
        assertFalse(savedProgress.captured.syncedToServer)
        saveJob.cancel()
    }

    @Test
    fun `a successful paired push flips the row to synced`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        coEvery { api.updatePosition("pair", 42, any()) } returns Response.success(positionResponse())

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000)

        coVerify(exactly = 1) { bookmarkDao.markSynced(TEST_SCOPE, 42) }
    }

    @Test
    fun `a successful standalone push flips the progress row to synced`() = runTest {
        coEvery { api.updatePosition("audiobook", 7, any()) } returns
            Response.success(positionResponse(scope = "audiobook", pairId = null, audiobookId = 7))

        repository().savePlaybackPositionStandalone(audiobookId = 7, audioPositionMs = 3_000)

        coVerify(exactly = 1) { userProgressDao.markSynced(TEST_SCOPE, "audiobook", 7) }
    }

    @Test
    fun `a failed paired push leaves the row unsynced`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        coEvery { api.updatePosition("pair", 42, any()) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000)

        coVerify(exactly = 0) { bookmarkDao.markSynced(any(), any()) }
    }

    // A cancelled save must be reported as cancellation, not logged as
    // "offline" and swallowed — the old catch (e: Exception) let the caller
    // continue into a Room write on an already-cancelled coroutine.

    @Test
    fun `updatePosition rethrows CancellationException`() = runTest {
        coEvery { api.updatePosition(any(), any(), any()) } throws CancellationException("cancelled")

        var thrown = false
        try {
            repository().updatePosition("pair", 42, PositionUpdateRequest())
        } catch (_: CancellationException) {
            thrown = true
        }
        assertTrue("CancellationException must propagate, not be logged as offline", thrown)
    }

    @Test
    fun `fetchPosition rethrows CancellationException`() = runTest {
        coEvery { api.getPosition(any(), any()) } throws CancellationException("cancelled")

        var thrown = false
        try {
            repository().fetchPosition("pair", 42)
        } catch (_: CancellationException) {
            thrown = true
        }
        assertTrue(thrown)
    }
}
