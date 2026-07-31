package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.BookmarkResponse
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PositionResponse
import com.booksync.data.remote.PositionUpdateRequest
import io.mockk.coEvery
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
        coEvery { bookmarkDao.getBookmark(42) } returns null
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
        val existing = BookmarkEntity(
            bookPairId = 42, source = "ebook", epubChapter = 5, epubSentenceIndex = 1,
            audioPositionMs = 1_000, updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(42) } returns existing
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
    fun `claimFormat=false falls back to the legacy endpoint without stamping source there either`() = runTest {
        val existing = BookmarkEntity(
            bookPairId = 42, source = "ebook", epubChapter = 5, epubSentenceIndex = 1,
            audioPositionMs = 1_000, updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(42) } returns existing
        coEvery { api.updatePosition("pair", 42, any()) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))
        coEvery { api.updateBookmark(any(), any()) } returns
            Response.success(mockk<BookmarkResponse>(relaxed = true))

        val savedBookmark = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(savedBookmark)) } returns Unit

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000, claimFormat = false)

        assertEquals("ebook", savedBookmark.captured.source)
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
        coEvery { bookmarkDao.getBookmark(42) } returns null
        val sentRequest = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 42, capture(sentRequest)) } returns
            Response.success(positionResponse())

        repository().savePlaybackPosition(pairId = 42, audioPositionMs = 5_000)

        assertEquals("audiobook", sentRequest.captured.source)
    }
}
