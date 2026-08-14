package com.booksync.data.repository

import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.PositionHintResponse
import com.booksync.data.remote.PositionResponse
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.local.dao.BookmarkDao
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.refreshBookmark] is the pull half of cross-device
 * position sync (issue #40): without it a device restores whatever it last
 * wrote itself and never learns about a position set elsewhere. The reader
 * calls it before restoring, so its decision rules are load-bearing.
 */
class RefreshBookmarkTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = bookmarkDao,
        pendingSyncDao = mockk(relaxed = true),
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
    )

    private fun serverPosition(
        chapter: Int = 39,
        locator: String? = null,
        updatedAt: String = "2026-07-30T16:49:36Z",
    ) = Response.success(
        PositionResponse(
            scope = "pair",
            book_pair_id = 309,
            source = "ebook",
            anchor_revision = 2,
            epub_chapter = chapter,
            epub_sentence_index = 0,
            audio_position_ms = 0,
            updated_at = updatedAt,
            device_name = "Web · Firefox",
            hints = locator?.let {
                listOf(PositionHintResponse(
                    kind = "readium_locator", device_id = "pixel", value = it,
                    anchor_revision = 2, current = true,
                ))
            } ?: emptyList(),
        )
    )

    private fun localBookmark(
        chapter: Int = 37,
        locator: String? = "{\"href\":\"index_split_037.html\"}",
        updatedAt: String = "1000",
        synced: Boolean = true,
    ) = BookmarkEntity(
        bookPairId = 309,
        source = "ebook",
        epubChapter = chapter,
        epubSentenceIndex = 0,
        audioPositionMs = 0,
        epubLocator = locator,
        locatorAudioMs = 54_373_980,
        updatedAt = updatedAt,
        syncedToServer = synced,
    )

    @Test
    fun `adopts a newer server position written by another device`() = runTest {
        // The web reader moved the anchor to chapter 39. The new anchor must
        // land locally — without this pull the reader restores its own stale
        // page and never learns about the other device.
        coEvery { api.getPosition("pair", 309) } returns serverPosition()
        coEvery { bookmarkDao.getBookmark(309) } returns localBookmark()

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().refreshBookmark(309)

        assertEquals(39, saved.captured.epubChapter)
        // The web reader has no Readium locator to send, so the server returns
        // none. The local one is kept: a hint whose anchor has moved is stale,
        // not worthless, and destroying it is what left the reader with no
        // position to restore and let chapter 0 be written over a real one.
        assertEquals("{\"href\":\"index_split_037.html\"}", saved.captured.epubLocator)
    }

    @Test
    fun `keeps unsynced local writes instead of clobbering them with the server copy`() = runTest {
        // Offline reading must survive a refresh, so an unsynced local row wins
        // regardless of timestamps.
        coEvery { api.getPosition("pair", 309) } returns serverPosition()
        coEvery { bookmarkDao.getBookmark(309) } returns localBookmark(synced = false)

        repository().refreshBookmark(309)

        coVerify(exactly = 0) { bookmarkDao.upsertBookmark(any()) }
    }

    @Test
    fun `keeps a newer local position over an older server one`() = runTest {
        coEvery { api.getPosition("pair", 309) } returns serverPosition(
            updatedAt = "2026-07-30T16:00:00Z")
        coEvery { bookmarkDao.getBookmark(309) } returns localBookmark(
            updatedAt = java.time.Instant.parse("2026-07-30T16:49:00Z").toEpochMilli().toString())

        repository().refreshBookmark(309)

        coVerify(exactly = 0) { bookmarkDao.upsertBookmark(any()) }
    }

    @Test
    fun `falls back to the local cache when the server is unreachable`() = runTest {
        coEvery { api.getPosition("pair", 309) } throws java.io.IOException("offline")
        coEvery { bookmarkDao.getBookmark(309) } returns localBookmark()

        repository().refreshBookmark(309)  // must not throw

        coVerify(exactly = 0) { bookmarkDao.upsertBookmark(any()) }
    }
}
