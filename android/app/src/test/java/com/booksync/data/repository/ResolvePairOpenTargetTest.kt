package com.booksync.data.repository

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [BookSyncRepository.resolvePairOpenTarget] had zero unit coverage (issue
 * #61/#40). It's the function that decides which format a tap on a pair
 * opens — it reads the local bookmark's `source`, which is exactly what
 * [PositionSavePolicy]'s `LocalMetadataOnly` verdict writes (via
 * `updateBookmarkMetadata`) even when the reader's restore never resolved a
 * real position. If this routing broke, a session saved through that path
 * would silently stop landing back in the reader.
 */
class ResolvePairOpenTargetTest {

    private val bookPairDao = mockk<BookPairDao>()
    private val bookmarkDao = mockk<BookmarkDao>()

    private fun repository() = BookSyncRepository(
        api = mockk<BookSyncApi>(relaxed = true),
        bookPairDao = bookPairDao,
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
        userScopeProvider = testScopeProvider(),
    )

    private fun pair(
        id: Int = 84,
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
    ) = BookPairEntity(
        id = id,
        ebookId = 1,
        ebookTitle = "Mad Ship",
        ebookAuthor = "Robin Hobb",
        ebookFilename = "mad-ship.epub",
        ebookFormat = "epub",
        audiobookId = 2,
        audiobookTitle = "Mad Ship",
        audiobookAuthor = "Robin Hobb",
        audiobookFilename = "mad-ship.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = "paired",
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
    )

    private fun bookmark(source: String) = BookmarkEntity(scopeKey = TEST_SCOPE, 
        bookPairId = 84,
        source = source,
        epubChapter = null,
        epubSentenceIndex = null,
        audioPositionMs = null,
        updatedAt = "1000",
    )

    @Test
    fun `source=ebook with ebook downloaded opens the Reader`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("ebook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = true))

        assertEquals(PairOpenTarget.Reader, target)
    }

    @Test
    fun `source=audiobook with audiobook downloaded opens the Player`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("audiobook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = true))

        assertEquals(PairOpenTarget.Player, target)
    }

    @Test
    fun `no bookmark falls back to whichever format is downloaded, preferring ebook`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns null

        assertEquals(
            PairOpenTarget.Reader,
            repository().resolvePairOpenTarget(pair(ebookDownloaded = true, audiobookDownloaded = true)))
        assertEquals(
            PairOpenTarget.Player,
            repository().resolvePairOpenTarget(pair(ebookDownloaded = false, audiobookDownloaded = true)))
        assertEquals(
            PairOpenTarget.Details,
            repository().resolvePairOpenTarget(pair(ebookDownloaded = false, audiobookDownloaded = false)))
    }

    @Test
    fun `source names a format that is not downloaded falls back to the downloaded one`() = runTest {
        // e.g. the audiobook was deleted locally after the last audio save.
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("audiobook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = false))

        assertEquals(PairOpenTarget.Reader, target)
    }

    @Test
    fun `a session saved via LocalMetadataOnly stamps source=ebook and still routes to the Reader`() = runTest {
        // PositionSavePolicy.LocalMetadataOnly (updateBookmarkMetadata) writes
        // ONLY source/updatedAt, leaving epubChapter/epubLocator/etc. null —
        // exactly what an unresolved restore looks like. Routing must not
        // depend on those anchors being populated.
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 84,
            source = "ebook",
            epubChapter = null,
            epubSentenceIndex = null,
            audioPositionMs = null,
            epubLocator = null,
            updatedAt = "1000",
        )

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = true))

        assertEquals(PairOpenTarget.Reader, target)
    }

    @Test
    fun `resolvePairOpenTarget by pairId looks up the pair first`() = runTest {
        coEvery { bookPairDao.getPairById(84) } returns pair(ebookDownloaded = true)
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("ebook")

        assertEquals(PairOpenTarget.Reader, repository().resolvePairOpenTarget(84))
    }

    @Test
    fun `resolvePairOpenTarget by pairId returns Details when the pair is unknown locally`() = runTest {
        coEvery { bookPairDao.getPairById(999) } returns null

        assertEquals(PairOpenTarget.Details, repository().resolvePairOpenTarget(999))
    }
}
