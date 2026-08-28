package com.booksync.data.repository

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.AudioBookResponse
import com.booksync.data.remote.BookPairResponse
import com.booksync.data.remote.EBookResponse
import com.booksync.data.remote.PageResponse
import com.booksync.data.remote.SyncMapResponse
import com.booksync.data.remote.SyncPointDto
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Sync points are cached in Room and, before issue #55, were only ever refetched
 * on an explicit download or a user tapping "Refresh sync data". Re-transcribing
 * a pair rebuilds the server's map under a new `version` with new timestamps, so
 * a stale cache kept converting positions against audio times that no longer
 * exist — `epubToAudioText` found the right text and returned the *old* second.
 *
 * These tests pin the invalidation contract:
 *
 * - the cache records which version it holds;
 * - a library refresh that sees a **different** server version drops the points
 *   outright, so a stale map can never be used for a conversion;
 * - a **null** server version means *unknown* (the endpoint didn't load it), not
 *   "no map", and must leave the cache alone;
 * - recovery is lazy and automatic at the paths that read sync points.
 */
class SyncMapVersionTest {

    private val api = mockk<BookSyncApi>()
    private val bookPairDao = mockk<BookPairDao>(relaxed = true)
    private val syncPointDao = mockk<SyncPointDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = bookPairDao,
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = syncPointDao,
        bookmarkDao = mockk(relaxed = true),
        pendingSyncDao = mockk(relaxed = true),
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun remotePair(syncMapVersion: Int?) = BookPairResponse(
        id = 42,
        ebook = EBookResponse(
            id = 1, title = "E", filename = "e.epub", format = "epub",
            uploaded_at = "2026-01-01T00:00:00Z",
        ),
        audiobook = AudioBookResponse(
            id = 2, title = "A", filename = "a.m4b", format = "m4b",
            uploaded_at = "2026-01-01T00:00:00Z",
        ),
        status = "synced",
        sync_map_version = syncMapVersion,
    )

    private fun page(vararg pairs: BookPairResponse) =
        PageResponse(items = pairs.toList(), total = pairs.size, page = 1, limit = 500)

    private fun cachedPair(syncMapVersion: Int?, downloaded: Boolean = true) = BookPairEntity(
        id = 42,
        ebookId = 1, ebookTitle = "E", ebookAuthor = null, ebookFilename = "e.epub",
        ebookFormat = "epub",
        audiobookId = 2, audiobookTitle = "A", audiobookAuthor = null,
        audiobookFilename = "a.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = "synced",
        ebookDownloaded = true,
        audiobookDownloaded = true,
        syncMapDownloaded = downloaded,
        syncMapVersion = syncMapVersion,
    )

    private fun syncMapResponse(version: Int) = SyncMapResponse(
        id = 5, book_pair_id = 42, version = version,
        total_sentences = 1, total_chapters = 1,
        created_at = "2026-01-01T00:00:00Z",
        sync_points = listOf(SyncPointDto(
            epub_chapter = 0, epub_sentence_index = 0,
            epub_text_preview = "a line of the book", audio_start_ms = 0,
            audio_end_ms = 1_000, confidence = 1f,
        )),
    )

    // --- the cache records what it holds -------------------------------------

    @Test
    fun `downloadSyncMap stamps the version it just cached`() = runTest {
        coEvery { api.getSyncMap(42) } returns syncMapResponse(version = 3)

        repository().downloadSyncMap(42)

        coVerify { bookPairDao.setSyncMapCached(42, true, 3) }
    }

    // --- a version bump drops the cache --------------------------------------

    @Test
    fun `refreshPairs drops the cached points when the server version moved`() = runTest {
        coEvery { api.getPairs(any(), any()) } returns page(remotePair(syncMapVersion = 4))
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(syncMapVersion = 3)
        val saved = slot<List<BookPairEntity>>()
        coEvery { bookPairDao.upsertPairs(capture(saved)) } returns Unit

        repository().refreshPairs()

        coVerify { syncPointDao.deletePointsForPair(42) }
        val entity = saved.captured.single()
        assertFalse("stale cache must not read as downloaded", entity.syncMapDownloaded)
        assertNull("stale cache must not claim a version", entity.syncMapVersion)
    }

    @Test
    fun `refreshPairs keeps the cache when the version matches`() = runTest {
        coEvery { api.getPairs(any(), any()) } returns page(remotePair(syncMapVersion = 3))
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(syncMapVersion = 3)
        val saved = slot<List<BookPairEntity>>()
        coEvery { bookPairDao.upsertPairs(capture(saved)) } returns Unit

        repository().refreshPairs()

        coVerify(exactly = 0) { syncPointDao.deletePointsForPair(any()) }
        val entity = saved.captured.single()
        assertTrue(entity.syncMapDownloaded)
        assertEquals(3, entity.syncMapVersion)
    }

    @Test
    fun `refreshPairs leaves the cache alone when the server reports no version`() = runTest {
        // Null is "unknown" — an endpoint that didn't load the relationship —
        // not "this pair has no map". Dropping on null would wipe a good cache.
        coEvery { api.getPairs(any(), any()) } returns page(remotePair(syncMapVersion = null))
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(syncMapVersion = 3)
        val saved = slot<List<BookPairEntity>>()
        coEvery { bookPairDao.upsertPairs(capture(saved)) } returns Unit

        repository().refreshPairs()

        coVerify(exactly = 0) { syncPointDao.deletePointsForPair(any()) }
        val entity = saved.captured.single()
        assertTrue(entity.syncMapDownloaded)
        assertEquals(3, entity.syncMapVersion)
    }

    @Test
    fun `refreshPairs drops a cache that never recorded a version`() = runTest {
        // Upgrading from a build predating the column: the points are present
        // but we cannot tell which map they came from, so they get refetched
        // once. "Probably still fine" is the reasoning that produced issue #55.
        coEvery { api.getPairs(any(), any()) } returns page(remotePair(syncMapVersion = 4))
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(syncMapVersion = null)
        val saved = slot<List<BookPairEntity>>()
        coEvery { bookPairDao.upsertPairs(capture(saved)) } returns Unit

        repository().refreshPairs()

        coVerify { syncPointDao.deletePointsForPair(42) }
        assertFalse(saved.captured.single().syncMapDownloaded)
    }

    @Test
    fun `refreshPairs does not churn on a pair that has no cached map at all`() = runTest {
        // Nothing downloaded: there is nothing to invalidate, and issuing a
        // delete per pair on every library refresh would be pure noise.
        coEvery { api.getPairs(any(), any()) } returns page(remotePair(syncMapVersion = 4))
        coEvery { bookPairDao.getPairById(42) } returns
            cachedPair(syncMapVersion = null, downloaded = false)
        val saved = slot<List<BookPairEntity>>()
        coEvery { bookPairDao.upsertPairs(capture(saved)) } returns Unit

        repository().refreshPairs()

        coVerify(exactly = 0) { syncPointDao.deletePointsForPair(any()) }
        assertFalse(saved.captured.single().syncMapDownloaded)
    }

    // --- recovery is lazy and automatic --------------------------------------

    @Test
    fun `ensureSyncMapCached refetches when the cache was dropped`() = runTest {
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(null, downloaded = false)
        coEvery { api.getSyncMap(42) } returns syncMapResponse(version = 4)

        assertTrue(repository().ensureSyncMapCached(42))

        coVerify { api.getSyncMap(42) }
        coVerify { bookPairDao.setSyncMapCached(42, true, 4) }
    }

    @Test
    fun `ensureSyncMapCached is a no-op when the cache is current`() = runTest {
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(syncMapVersion = 3)

        assertTrue(repository().ensureSyncMapCached(42))

        coVerify(exactly = 0) { api.getSyncMap(any()) }
    }

    @Test
    fun `ensureSyncMapCached swallows a failed refetch`() = runTest {
        // Called from reader/player open. A server that can't be reached must
        // not take the screen down with it.
        coEvery { bookPairDao.getPairById(42) } returns cachedPair(null, downloaded = false)
        coEvery { api.getSyncMap(42) } throws java.io.IOException("offline")

        assertFalse(repository().ensureSyncMapCached(42))
    }
}
