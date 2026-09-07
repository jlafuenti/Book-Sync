package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.remote.BookSyncApi
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
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

    private fun repository() = buildRepository(
        bookmarkDao = bookmarkDao,
    )

    @Test
    fun `stamps source but leaves anchors untouched`() = runTest {
        val existing = BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 42,
            source = "audiobook",
            epubChapter = 39,
            epubSentenceIndex = 12,
            audioPositionMs = 54_000,
            epubLocator = "{\"href\":\"ch39.xhtml\"}",
            locatorAudioMs = 54_000,
            updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns existing

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
    fun `does not bump updatedAt or capturedAt on an existing row`() = runTest {
        // Issue #61/#40 fix 5: bumping updatedAt here used to make
        // refreshBookmark think this local row was newer than it really is,
        // which blocks pulling a genuinely newer position from another
        // device on the next open. Only `source` should change.
        val existing = BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 42,
            source = "audiobook",
            epubChapter = 39,
            epubSentenceIndex = 12,
            audioPositionMs = 54_000,
            updatedAt = "1000",
            capturedAt = "2026-01-01T00:00:00",
        )
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns existing

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertEquals("1000", saved.captured.updatedAt)
        assertEquals("2026-01-01T00:00:00", saved.captured.capturedAt)
    }

    @Test
    fun `keeps the sync-map version the stored sentence index was resolved against`() = runTest {
        // Issue #116: the version travels with the index it describes, and this
        // call touches neither. Dropping it would leave a sentence index that
        // attests to no map at all, so its next push could not be told apart
        // from one resolved against whatever map happens to be live then.
        val existing = BookmarkEntity(
            scopeKey = TEST_SCOPE,
            bookPairId = 42,
            source = "audiobook",
            epubChapter = 39,
            epubSentenceIndex = 12,
            syncMapVersion = 4,
            audioPositionMs = 54_000,
            updatedAt = "1000",
        )
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns existing

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertEquals(4, saved.captured.syncMapVersion)
    }

    @Test
    fun `keeps the device attribution of the write it is merging onto`() = runTest {
        // The row still describes the position that other device recorded; this
        // call only re-labels which format the user was last in. Re-stamping it
        // with this device would make the conflict resolver treat a foreign
        // write as local on the next 409.
        val existing = BookmarkEntity(
            scopeKey = TEST_SCOPE,
            bookPairId = 42,
            source = "audiobook",
            epubChapter = 39,
            epubSentenceIndex = 12,
            audioPositionMs = 54_000,
            updatedAt = "1000",
            deviceId = "other-device",
            deviceName = "Kitchen tablet",
        )
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns existing

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertEquals("other-device", saved.captured.deviceId)
        assertEquals("Kitchen tablet", saved.captured.deviceName)
    }

    @Test
    fun `never calls the server or enqueues pending_sync`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null

        val api = mockk<BookSyncApi>(relaxed = true)
        val pendingSyncDao = mockk<com.booksync.data.local.dao.PendingSyncDao>(relaxed = true)
        val repo = buildRepository(
            api = api,
            bookmarkDao = bookmarkDao,
            pendingSyncDao = pendingSyncDao,
        )

        repo.updateBookmarkMetadata(42, source = "ebook")

        coVerify(exactly = 0) { pendingSyncDao.insert(any()) }
        coVerify(exactly = 0) { api.updatePosition(any(), any(), any()) }
    }

    @Test
    fun `creates a bare row with no anchors when none exists yet`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null

        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertEquals("ebook", saved.captured.source)
        assertNull(saved.captured.epubChapter)
        assertNull(saved.captured.epubLocator)
    }

    @Test
    fun `marks the row synced so it is never picked up by the startup retry sweep`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 42) } returns null
        val saved = slot<BookmarkEntity>()
        coEvery { bookmarkDao.upsertBookmark(capture(saved)) } returns Unit

        repository().updateBookmarkMetadata(42, source = "ebook")

        assertTrue(saved.captured.syncedToServer)
    }
}
