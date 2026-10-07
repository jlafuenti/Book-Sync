package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.dao.SyncPointWordsDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.SyncPointWordsEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.SyncMapResponse
import com.booksync.data.remote.SyncMapWordsResponse
import com.booksync.data.remote.SyncPointWordsDto
import com.booksync.diagnostics.DiagnosticLogger
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import java.io.IOException
import java.nio.file.Files
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Per-word timing is fetched and cached beside the sync map (issue #836). */
class MediaDownloadRepositoryWordsTest {

    /** In-memory stand-in for the `sync_point_words` table. */
    private class FakeWordsDao : SyncPointWordsDao {
        val rows = mutableListOf<SyncPointWordsEntity>()
        override suspend fun getForPair(pairId: Int) = rows.filter { it.bookPairId == pairId }
        override suspend fun insertAll(rows: List<SyncPointWordsEntity>) {
            rows.forEach { r ->
                this.rows.removeAll {
                    it.bookPairId == r.bookPairId && it.epubChapter == r.epubChapter &&
                        it.epubSentenceIndex == r.epubSentenceIndex
                }
                this.rows.add(r)
            }
        }
        override suspend fun deleteForPair(pairId: Int) { rows.removeAll { it.bookPairId == pairId } }
        override suspend fun countForPair(pairId: Int) = rows.count { it.bookPairId == pairId }
    }

    private val api = mockk<BookSyncApi>(relaxed = true)
    private val bookPairDao = mockk<BookPairDao>(relaxed = true)
    private val syncPointDao = mockk<SyncPointDao>(relaxed = true)
    private val wordsDao = FakeWordsDao()
    private lateinit var context: Context
    private var currentPair: BookPairEntity? = null

    private fun pair(syncMapDownloaded: Boolean, version: Int? = 3) = BookPairEntity(
        id = 42, ebookId = 7, ebookTitle = "Axis Test", ebookAuthor = "Author",
        ebookFilename = "axis.epub", ebookFormat = "epub",
        audiobookId = 9, audiobookTitle = "Axis Test", audiobookAuthor = "Author",
        audiobookFilename = "axis.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 1_000, status = "synced",
        ebookDownloaded = true, audiobookDownloaded = true,
        syncMapDownloaded = syncMapDownloaded, syncMapVersion = version,
    )

    @Before
    fun setUp() {
        context = mockk(relaxed = true)
        every { context.filesDir } returns Files.createTempDirectory("words-repo-test").toFile()
        currentPair = pair(syncMapDownloaded = true)
        coEvery { bookPairDao.getPairById(any()) } answers { currentPair }
    }

    private fun repo() = buildMediaDownloadRepository(
        api = api,
        bookPairDao = bookPairDao,
        syncPointDao = syncPointDao,
        syncPointWordsDao = wordsDao,
        context = context,
        diagnosticLogger = mockk<DiagnosticLogger>(relaxed = true),
    )

    private fun syncMap(version: Int = 3) = SyncMapResponse(
        id = 1, book_pair_id = 42, version = version, total_sentences = 0, total_chapters = 0,
        created_at = "2026-01-01T00:00:00Z", sync_points = emptyList(),
    )

    private fun words(version: Int = 3) = SyncMapWordsResponse(
        sync_map_id = 1, version = version,
        points = listOf(
            SyncPointWordsDto(2, 0, listOf(100, 450, 900)),
            SyncPointWordsDto(2, 1, listOf(1_000, 1_300)),
        ),
    )

    @Test
    fun `downloading a sync map stores the word timing rows`() = runTest {
        coEvery { api.getSyncMap(42) } returns syncMap()
        coEvery { api.getSyncMapWords(42) } returns words()

        repo().downloadSyncMap(42)

        assertEquals(2, wordsDao.rows.size)
        assertEquals("100,450,900", wordsDao.rows.first { it.epubSentenceIndex == 0 }.wordStarts)
    }

    @Test
    fun `old rows are replaced rather than appended`() = runTest {
        wordsDao.rows += SyncPointWordsEntity(42, 9, 9, "1,2")
        coEvery { api.getSyncMap(42) } returns syncMap()
        coEvery { api.getSyncMapWords(42) } returns words()

        repo().downloadSyncMap(42)

        assertTrue(wordsDao.rows.none { it.epubChapter == 9 })
        assertEquals(2, wordsDao.rows.size)
    }

    @Test
    fun `a failing words fetch leaves the sync map saved`() = runTest {
        coEvery { api.getSyncMap(42) } returns syncMap()
        coEvery { api.getSyncMapWords(42) } throws IOException("offline")

        repo().downloadSyncMap(42)

        coVerify(exactly = 1) { bookPairDao.setSyncMapCached(42, true, 3) }
        assertTrue(wordsDao.rows.isEmpty())
    }

    @Test
    fun `a words response for another version is not stored`() = runTest {
        coEvery { api.getSyncMap(42) } returns syncMap(version = 3)
        coEvery { api.getSyncMapWords(42) } returns words(version = 2)

        repo().downloadSyncMap(42)

        assertTrue(wordsDao.rows.isEmpty())
    }

    @Test
    fun `clearing the sync map cache drops the words with the points`() = runTest {
        wordsDao.rows += SyncPointWordsEntity(42, 2, 0, "1,2")
        wordsDao.rows += SyncPointWordsEntity(43, 2, 0, "1,2")

        repo().clearSyncMapCache(42)

        assertEquals(listOf(43), wordsDao.rows.map { it.bookPairId })
        coVerify(exactly = 1) { syncPointDao.deletePointsForPair(42) }
    }

    @Test
    fun `ensureSyncPointWords fetches when the pair has points but no words`() = runTest {
        coEvery { api.getSyncMapWords(42) } returns words()

        val ok = repo().ensureSyncPointWords(42)

        assertTrue(ok)
        assertEquals(2, wordsDao.rows.size)
    }

    @Test
    fun `ensureSyncPointWords does not hit the network when words are cached`() = runTest {
        wordsDao.rows += SyncPointWordsEntity(42, 2, 0, "1,2")

        val ok = repo().ensureSyncPointWords(42)

        assertTrue(ok)
        coVerify(exactly = 0) { api.getSyncMapWords(any()) }
    }

    @Test
    fun `ensureSyncPointWords does nothing without a cached sync map`() = runTest {
        currentPair = pair(syncMapDownloaded = false)

        val ok = repo().ensureSyncPointWords(42)

        assertFalse(ok)
        coVerify(exactly = 0) { api.getSyncMapWords(any()) }
    }

    @Test
    fun `ensureSyncPointWords reports false on a network failure`() = runTest {
        coEvery { api.getSyncMapWords(42) } throws IOException("offline")

        assertFalse(repo().ensureSyncPointWords(42))
        assertTrue(wordsDao.rows.isEmpty())
    }

    @Test
    fun `ensureSyncPointWords reports false when the server has no words`() = runTest {
        coEvery { api.getSyncMapWords(42) } returns SyncMapWordsResponse(1, 3, emptyList())

        assertFalse(repo().ensureSyncPointWords(42))
    }

    @Test
    fun `getSyncPointWords maps rows to start arrays keyed by chapter and sentence`() = runTest {
        wordsDao.rows += SyncPointWordsEntity(42, 2, 0, "100,450,900")
        wordsDao.rows += SyncPointWordsEntity(42, 3, 5, "7")

        val map = repo().getSyncPointWords(42)

        assertEquals(setOf(2 to 0, 3 to 5), map.keys)
        assertArrayEquals(intArrayOf(100, 450, 900), map.getValue(2 to 0))
        assertArrayEquals(intArrayOf(7), map.getValue(3 to 5))
    }
}
