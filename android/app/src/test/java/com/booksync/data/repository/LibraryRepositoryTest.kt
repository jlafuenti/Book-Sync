package com.booksync.data.repository

import com.booksync.data.local.dao.AudioBookDao
import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.EBookDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Direct tests for [LibraryRepository] (issue #224), covering the moved code
 * that had no test of its own: the unpaired-item lookups behind the pairing
 * screen, the Continue flows' exclusion of completed items, and the
 * last-opened map the library's "Recently opened" sort reads (issue #223).
 */
class LibraryRepositoryTest {

    private val bookPairDao = mockk<BookPairDao>(relaxed = true)
    private val eBookDao = mockk<EBookDao>(relaxed = true)
    private val audioBookDao = mockk<AudioBookDao>(relaxed = true)
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)

    private fun library() = buildLibraryRepository(
        bookPairDao = bookPairDao,
        eBookDao = eBookDao,
        audioBookDao = audioBookDao,
        bookmarkDao = bookmarkDao,
        userProgressDao = userProgressDao,
    )

    private fun pair(id: Int, ebookId: Int, audiobookId: Int) = BookPairEntity(
        id = id, ebookId = ebookId, ebookTitle = "E$ebookId", ebookAuthor = null,
        ebookFilename = "e$ebookId.epub", ebookFormat = "epub", audiobookId = audiobookId,
        audiobookTitle = "A$audiobookId", audiobookAuthor = null, audiobookFilename = "a$audiobookId.m4b",
        audiobookFormat = "m4b", audiobookDurationSeconds = null, status = "synced",
    )

    private fun ebook(id: Int) = EBookEntity(
        id = id, title = "E$id", author = null, filename = "e$id.epub", fileSize = null,
        format = "epub", series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
    )

    private fun audiobook(id: Int) = AudioBookEntity(
        id = id, title = "A$id", author = null, filename = "a$id.m4b", durationSeconds = null,
        format = "m4b", series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
    )

    private fun progress(mediaType: String, mediaId: Int, completed: Boolean, updatedAt: Long = 0L) =
        UserProgressEntity(
            scopeKey = TEST_SCOPE, mediaType = mediaType, mediaId = mediaId, bookPairId = null,
            epubCfi = null, epubChapter = null, epubProgressPercent = null, audioPositionMs = null,
            isCompleted = completed, updatedAt = updatedAt, deviceId = null,
        )

    @Test
    fun `unpaired lookups exclude every item that already belongs to a pair`() = runTest {
        coEvery { eBookDao.getAllEBooksOnce() } returns listOf(ebook(1), ebook(2), ebook(3))
        coEvery { audioBookDao.getAllAudioBooksOnce() } returns listOf(audiobook(10), audiobook(11))
        coEvery { bookPairDao.getAllPairsOnce() } returns listOf(pair(100, ebookId = 2, audiobookId = 11))

        val repo = library()

        assertEquals(listOf(1, 3), repo.getUnpairedEbooks().map { it.id })
        assertEquals(listOf(10), repo.getUnpairedAudiobooks().map { it.id })
    }

    @Test
    fun `continue lists drop items whose progress row says completed`() = runTest {
        every { bookPairDao.getRecentlyPlayedPairs(TEST_SCOPE) } returns
            flowOf(listOf(pair(1, ebookId = 70, audiobookId = 700), pair(2, ebookId = 71, audiobookId = 701)))
        every { audioBookDao.getRecentlyPlayedStandaloneAudiobooks(TEST_SCOPE) } returns
            flowOf(listOf(audiobook(700), audiobook(701)))
        every { eBookDao.getRecentlyReadEbooks(TEST_SCOPE) } returns
            flowOf(listOf(ebook(70), ebook(71)))
        every { userProgressDao.getAllProgressFlow(TEST_SCOPE) } returns flowOf(
            listOf(
                progress("audiobook", 701, completed = true),
                progress("ebook", 70, completed = true),
                // A finished *ebook* must not hide the audiobook side of a pair.
                progress("ebook", 71, completed = false),
            )
        )

        val repo = library()

        assertEquals(listOf(1), repo.getRecentlyPlayedPairsFlow().first().map { it.id })
        assertEquals(listOf(700), repo.getRecentlyPlayedStandaloneAudiobooksFlow().first().map { it.id })
        assertEquals(listOf(71), repo.getRecentlyReadEbooksFlow().first().map { it.id })
    }

    @Test
    fun `last-opened times prefer capturedAt for pairs and split progress rows by kind`() = runTest {
        every { bookmarkDao.getAllBookmarksFlow(TEST_SCOPE) } returns flowOf(
            listOf(
                BookmarkEntity(
                    scopeKey = TEST_SCOPE, bookPairId = 7, source = "ebook", epubChapter = 1,
                    epubSentenceIndex = 0, audioPositionMs = 0, updatedAt = "1000",
                    capturedAt = "2026-08-01T10:00:00Z",
                ),
                BookmarkEntity(
                    scopeKey = TEST_SCOPE, bookPairId = 8, source = "ebook", epubChapter = 1,
                    epubSentenceIndex = 0, audioPositionMs = 0, updatedAt = "2000",
                ),
            )
        )
        every { userProgressDao.getAllProgressFlow(TEST_SCOPE) } returns flowOf(
            listOf(
                progress("ebook", 7, completed = false, updatedAt = 5L),
                progress("audiobook", 7, completed = false, updatedAt = 6L),
            )
        )

        val times = library().lastOpenedTimesFlow().first()

        assertEquals(parseSyncTimestamp("2026-08-01T10:00:00Z"), times.pairs[7])
        assertEquals(2000L, times.pairs[8])
        assertEquals(mapOf(7 to 5L), times.ebooks)
        assertEquals(mapOf(7 to 6L), times.audiobooks)
    }
}
