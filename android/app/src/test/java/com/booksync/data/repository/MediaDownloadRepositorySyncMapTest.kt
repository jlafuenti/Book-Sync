package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.SyncMapResponse
import com.booksync.diagnostics.DiagnosticLogger
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Before
import org.junit.Test
import java.nio.file.Files

/**
 * Sync maps follow the last download (issue #678).
 *
 * [MediaDownloadRepository.deleteEbook] / `deleteAudiobook` clear the cached
 * sync map once neither format is downloaded any more, and [MediaDownloadRepository.downloadSyncMap]
 * clears the "removed by hand" mark on a successful fetch. The one subtle
 * failure mode this pins is the stale-snapshot trap: several ViewModels
 * delete both halves of a pair from one `BookPairEntity` fetched before
 * either delete ran (`if (pair.ebookDownloaded) repository.deleteEbook(pair)`
 * then `if (pair.audiobookDownloaded) repository.deleteAudiobook(pair)`), so
 * the fix has to re-read the pair's current state rather than trust the
 * caller's copy — otherwise each call sees the other half as still present
 * and the map never clears.
 */
class MediaDownloadRepositorySyncMapTest {

    private val bookPairDao = mockk<BookPairDao>(relaxed = true)
    private val syncPointDao = mockk<SyncPointDao>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)
    private val api = mockk<BookSyncApi>(relaxed = true)
    private lateinit var context: Context

    /** In-memory stand-in for the `book_pairs` row this pair's id maps to —
     *  every DAO write below updates it, every DAO read returns it, so the
     *  test observes exactly what the repository would see from Room. */
    private lateinit var currentPair: BookPairEntity

    private fun pair(
        id: Int = 42,
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
        syncMapDownloaded: Boolean = false,
    ) = BookPairEntity(
        id = id,
        ebookId = 7,
        ebookTitle = "Axis Test",
        ebookAuthor = "Author",
        ebookFilename = "axis.epub",
        ebookFormat = "epub",
        audiobookId = 9,
        audiobookTitle = "Axis Test",
        audiobookAuthor = "Author",
        audiobookFilename = "axis.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = 1_000,
        status = "synced",
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
        syncMapDownloaded = syncMapDownloaded,
    )

    @Before
    fun setUp() {
        val tempDir = Files.createTempDirectory("media-download-repo-test").toFile()
        context = mockk(relaxed = true)
        every { context.filesDir } returns tempDir

        currentPair = pair(ebookDownloaded = true, audiobookDownloaded = true, syncMapDownloaded = true)
        coEvery { bookPairDao.getPairById(any()) } answers { currentPair }
        coEvery { bookPairDao.setEbookDownloaded(any(), any()) } answers {
            val downloaded = secondArg<Boolean>()
            currentPair = currentPair.copy(ebookDownloaded = downloaded)
        }
        coEvery { bookPairDao.setAudiobookDownloaded(any(), any()) } answers {
            val downloaded = secondArg<Boolean>()
            currentPair = currentPair.copy(audiobookDownloaded = downloaded)
        }
        coEvery { bookPairDao.setSyncMapCached(any(), any(), any()) } answers {
            val downloaded = secondArg<Boolean>()
            currentPair = currentPair.copy(syncMapDownloaded = downloaded)
        }
    }

    private fun repo() = buildMediaDownloadRepository(
        api = api,
        bookPairDao = bookPairDao,
        syncPointDao = syncPointDao,
        context = context,
        diagnosticLogger = mockk<DiagnosticLogger>(relaxed = true),
        syncMapRemovalStore = syncMapRemovalStore,
    )

    @Test
    fun `deleting the ebook with the audiobook still downloaded keeps the map`() = runTest {
        val stalePair = pair(ebookDownloaded = true, audiobookDownloaded = true, syncMapDownloaded = true)

        repo().deleteEbook(stalePair)

        coVerify(exactly = 0) { syncPointDao.deletePointsForPair(any()) }
        coVerify(exactly = 0) { bookPairDao.setSyncMapCached(any(), false, any()) }
    }

    @Test
    fun `deleting the audiobook after the ebook clears the map`() = runTest {
        val stalePair = pair(ebookDownloaded = true, audiobookDownloaded = true, syncMapDownloaded = true)
        val repository = repo()

        repository.deleteEbook(stalePair)   // leaves audiobook downloaded — map stays
        repository.deleteAudiobook(stalePair) // now nothing is downloaded — map clears

        coVerify(exactly = 1) { syncPointDao.deletePointsForPair(stalePair.id) }
        coVerify(exactly = 1) { bookPairDao.setSyncMapCached(stalePair.id, false, null) }
    }

    /**
     * The trap named in issue #678: both deletes driven from the SAME stale
     * `pair` snapshot, exactly like `LibraryViewModel.deletePair` /
     * `DownloadedViewModel.deletePair`:
     *   if (pair.ebookDownloaded) repository.deleteEbook(pair)
     *   if (pair.audiobookDownloaded) repository.deleteAudiobook(pair)
     * If the prune decision trusted `pair` instead of re-reading Room, each
     * call would see the other half's flag still `true` in the stale copy
     * and never clear the map.
     */
    @Test
    fun `both halves deleted in sequence from one stale snapshot still clears the map`() = runTest {
        val stalePair = pair(ebookDownloaded = true, audiobookDownloaded = true, syncMapDownloaded = true)
        val repository = repo()

        if (stalePair.ebookDownloaded) repository.deleteEbook(stalePair)
        if (stalePair.audiobookDownloaded) repository.deleteAudiobook(stalePair)

        coVerify(exactly = 1) { syncPointDao.deletePointsForPair(stalePair.id) }
        coVerify(exactly = 1) { bookPairDao.setSyncMapCached(stalePair.id, false, null) }
    }

    @Test
    fun `deleting a standalone-only pair with no sync map does nothing extra`() = runTest {
        currentPair = pair(ebookDownloaded = true, audiobookDownloaded = false, syncMapDownloaded = false)
        val stalePair = currentPair

        repo().deleteEbook(stalePair)

        // Nothing was ever cached, so clearing is a correct no-op — but it
        // must still run (there is no downloaded format left), just with an
        // empty table.
        coVerify(exactly = 1) { syncPointDao.deletePointsForPair(stalePair.id) }
    }

    @Test
    fun `a successful sync map download clears the removed-by-hand mark`() = runTest {
        coEvery { api.getSyncMap(42) } returns SyncMapResponse(
            id = 1,
            book_pair_id = 42,
            version = 3,
            total_sentences = 0,
            total_chapters = 0,
            created_at = "2026-01-01T00:00:00Z",
            sync_points = emptyList(),
        )

        repo().downloadSyncMap(42)

        coVerify(exactly = 1) { syncMapRemovalStore.clearRemoved(42) }
    }

    /**
     * The player waits a bounded time for the map at open, then cancels the
     * fetch and plays on. Cancelled between writing the points and stamping the
     * flag, the cache was left with every point on disk and
     * `syncMapDownloaded = false` — invisible to the prune, which reads the
     * flag, and to Refresh/Remove, which are gated on it (seen on the emulator,
     * issue #678). Once the response is in hand, the local write must finish.
     */
    @Test
    fun `a fetch cancelled mid-write still stamps the cache it wrote`() = runTest {
        coEvery { api.getSyncMap(42) } returns SyncMapResponse(
            id = 1,
            book_pair_id = 42,
            version = 3,
            total_sentences = 0,
            total_chapters = 0,
            created_at = "2026-01-01T00:00:00Z",
            sync_points = emptyList(),
        )
        coEvery { syncPointDao.insertPoints(any()) } coAnswers { kotlinx.coroutines.delay(1_000) }

        val job = launch { repo().downloadSyncMap(42) }
        testScheduler.advanceTimeBy(500)
        job.cancel()
        testScheduler.advanceUntilIdle()

        coVerify(exactly = 1) { bookPairDao.setSyncMapCached(42, true, 3) }
    }
}
