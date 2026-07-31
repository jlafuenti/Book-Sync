package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [BookSyncRepository.updateBookmarkMetadata] backs [PositionSavePolicy]'s
 * `LocalMetadataOnly` verdict (issue #61/#40): while a restore is unresolved
 * the reader view sits at spine 0, so a save must not overwrite the real
 * anchors (chapter/sentence/locator/audio) with that — that's exactly the
 * chapter-0 data loss this whole redesign exists to fix. But
 * `resolvePairOpenTarget` keys off the bookmark's `source`, so *something*
 * has to record "the user was last in the reader" even when the position
 * itself can't be trusted yet — this is that something.
 */
class UpdateBookmarkMetadataTest {

    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = mockk<BookSyncApi>(relaxed = true),
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

    @Test
    fun `stamps source and updatedAt but leaves anchors untouched`() = runTest {
        val existing = BookmarkEntity(
            bookPairId = 42,
            source = "audiobook",
            epubChapter = 39,
            epubSentenceIndex = 12,
            audioPositionMs = 54_000,
            epubLocator = "{\"href\":\"ch39.xhtml\"}",
            locatorAudioMs = 54_000,
            updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(42) } returns existing

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertEquals("ebook", saved.captured.source)
        assertEquals(39, saved.captured.epubChapter)
        assertEquals(12, saved.captured.epubSentenceIndex)
        assertEquals(54_000, saved.captured.audioPositionMs)
        assertEquals("{\"href\":\"ch39.xhtml\"}", saved.captured.epubLocator)
        assertEquals(54_000, saved.captured.locatorAudioMs)
    }

    @Test
    fun `never calls the server or enqueues pending_sync`() = runTest {
        coEvery { bookmarkDao.getBookmark(42) } returns null

        val api = mockk<BookSyncApi>(relaxed = true)
        val pendingSyncDao = mockk<com.booksync.data.local.dao.PendingSyncDao>(relaxed = true)
        val repo = BookSyncRepository(
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

        repo.updateBookmarkMetadata(42, source = "ebook")

        coVerify(exactly = 0) { pendingSyncDao.insert(any()) }
        coVerify(exactly = 0) { api.updateBookmark(any(), any()) }
        coVerify(exactly = 0) { api.updatePosition(any(), any(), any()) }
    }

    @Test
    fun `creates a bare row with no anchors when none exists yet`() = runTest {
        coEvery { bookmarkDao.getBookmark(42) } returns null

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertEquals("ebook", saved.captured.source)
        assertNull(saved.captured.epubChapter)
        assertNull(saved.captured.epubLocator)
    }

    @Test
    fun `marks the row synced so it is never picked up by the startup retry sweep`() = runTest {
        coEvery { bookmarkDao.getBookmark(42) } returns null
        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertTrue(saved.captured.syncedToServer)
    }
}
