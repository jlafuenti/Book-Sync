package com.booksync.data.repository

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.PageResponse
import com.booksync.data.remote.PositionResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * Issue #771: a pair's completion lives only on the server's pair bookmark,
 * and the startup reconcile used to drop it — `BookmarkEntity` has no
 * completion field, and the pair has no standalone-scope row for
 * `reconcileProgress` to read it from. Continue Reading hides a pair through
 * its local `user_progress` rows, so a fresh install listed every finished
 * pair, and a device that had finished one locally never saw another client
 * un-finish it. The reconcile now projects the pair row's `is_completed` onto
 * both local rows, in both directions.
 */
class PairCompletionPullTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)

    private fun positions() = buildPositionRepository(
        api = api, bookmarkDao = bookmarkDao, userProgressDao = userProgressDao,
    )

    private val pair = BookPairEntity(
        id = 7, ebookId = 70, ebookTitle = "E", ebookAuthor = null, ebookFilename = "e.epub",
        ebookFormat = "epub", audiobookId = 700, audiobookTitle = "A", audiobookAuthor = null,
        audiobookFilename = "a.m4b", audiobookFormat = "m4b", audiobookDurationSeconds = 3_600,
        status = "synced",
    )

    private fun pairRow(completed: Boolean, at: String = REMOTE_AT) = PositionResponse(
        scope = "pair", book_pair_id = 7, source = "audiobook", anchor_revision = 1,
        audio_position_ms = 3_500_000, is_completed = completed,
        captured_at = at, updated_at = at,
    )

    private fun localBookmark(at: String, synced: Boolean = true) = BookmarkEntity(
        scopeKey = TEST_SCOPE, bookPairId = 7, source = "audiobook", epubChapter = 3,
        epubSentenceIndex = 0, audioPositionMs = 3_500_000, updatedAt = "1000",
        capturedAt = at, syncedToServer = synced,
    )

    private fun localProgress(
        mediaType: String, mediaId: Int, completed: Boolean, at: String, synced: Boolean = true,
    ) = UserProgressEntity(
        scopeKey = TEST_SCOPE, mediaType = mediaType, mediaId = mediaId, bookPairId = 7,
        epubCfi = null, epubChapter = null, epubProgressPercent = 41f,
        audioPositionMs = 123_000, isCompleted = completed, updatedAt = 1_000L,
        deviceId = "this-device", syncedToServer = synced, capturedAt = at,
    )

    private fun bulk(vararg rows: PositionResponse) {
        coEvery { api.getPositions(1, any()) } returns PageResponse(
            items = rows.toList(), total = rows.size, page = 1, limit = 500,
        )
    }

    private fun localRows(ebook: UserProgressEntity?, audiobook: UserProgressEntity?) {
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "ebook", 70) } returns ebook
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 700) } returns audiobook
    }

    private fun upserted(): List<UserProgressEntity> {
        val written = mutableListOf<UserProgressEntity>()
        coVerify(atLeast = 0) { userProgressDao.upsertProgress(capture(written)) }
        return written
    }

    @Test
    fun `a finished pair pulled onto a fresh install flags both local progress rows`() = runTest {
        bulk(pairRow(completed = true))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns null
        localRows(ebook = null, audiobook = null)

        positions().syncAllBookmarksAndProgress(listOf(pair))

        val written = upserted()
        assertEquals(setOf("ebook" to 70, "audiobook" to 700),
            written.map { it.mediaType to it.mediaId }.toSet())
        written.forEach {
            assertTrue("${it.mediaType} row is completed", it.isCompleted)
            assertTrue("${it.mediaType} row is synced — never pushed back", it.syncedToServer)
            assertEquals(7, it.bookPairId)
            assertEquals(REMOTE_AT, it.capturedAt)
        }
    }

    @Test
    fun `the pulled completion takes the pair off Continue Reading`() = runTest {
        bulk(pairRow(completed = true))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns null
        localRows(ebook = null, audiobook = null)
        positions().syncAllBookmarksAndProgress(listOf(pair))
        val written = upserted()

        val bookPairDao = mockk<BookPairDao>(relaxed = true)
        val progressDao = mockk<UserProgressDao>(relaxed = true)
        every { bookPairDao.getRecentlyPlayedPairs(TEST_SCOPE) } returns flowOf(listOf(pair))
        every { progressDao.getAllProgressFlow(TEST_SCOPE) } returns flowOf(written)
        val library = buildLibraryRepository(bookPairDao = bookPairDao, userProgressDao = progressDao)

        assertEquals(emptyList<BookPairEntity>(), library.getRecentlyPlayedPairsFlow().first())
    }

    @Test
    fun `a pair un-finished elsewhere clears a synced local completion`() = runTest {
        bulk(pairRow(completed = false))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns localBookmark(OLDER_AT)
        localRows(
            ebook = localProgress("ebook", 70, completed = true, at = OLDER_AT),
            audiobook = localProgress("audiobook", 700, completed = true, at = OLDER_AT),
        )

        positions().syncAllBookmarksAndProgress(listOf(pair))

        val written = upserted()
        assertEquals(setOf("ebook" to 70, "audiobook" to 700),
            written.map { it.mediaType to it.mediaId }.toSet())
        written.forEach {
            assertEquals(false, it.isCompleted)
            assertTrue(it.syncedToServer)
            // Only the flag moves; the stored position is left alone.
            assertEquals(123_000, it.audioPositionMs)
            assertEquals(41f, it.epubProgressPercent)
            assertEquals(OLDER_AT, it.capturedAt)
        }
    }

    @Test
    fun `an unsynced local completion is not overwritten by the pull`() = runTest {
        // markPairComplete offline: both rows flagged, waiting for the sweep.
        bulk(pairRow(completed = false))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns localBookmark(OLDER_AT)
        localRows(
            ebook = localProgress("ebook", 70, completed = true, at = OLDER_AT, synced = false),
            audiobook = localProgress("audiobook", 700, completed = true, at = OLDER_AT, synced = false),
        )
        coEvery { api.updatePosition(any(), any(), any()) } returns Response.success(pairRow(true))

        positions().syncAllBookmarksAndProgress(listOf(pair))

        assertTrue(upserted().none { !it.isCompleted })
    }

    @Test
    fun `a local completion newer than the server row is not cleared`() = runTest {
        // Mark Complete tapped while the startup fetch was in flight.
        bulk(pairRow(completed = false))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns localBookmark(REMOTE_AT)
        localRows(
            ebook = localProgress("ebook", 70, completed = true, at = NEWER_AT),
            audiobook = localProgress("audiobook", 700, completed = true, at = NEWER_AT),
        )

        positions().syncAllBookmarksAndProgress(listOf(pair))

        assertTrue(upserted().none { !it.isCompleted })
    }

    @Test
    fun `an unsynced local bookmark skips the projection`() = runTest {
        // That bookmark is about to be pushed, and the server may recompute
        // completion from it — the pre-push flag is stale. The next sync applies it.
        bulk(pairRow(completed = true))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns localBookmark(NEWER_AT, synced = false)
        localRows(ebook = null, audiobook = null)
        coEvery { api.updatePosition("pair", 7, any()) } returns Response.success(pairRow(false, NEWER_AT))

        positions().syncAllBookmarksAndProgress(listOf(pair))

        coVerify(exactly = 1) { api.updatePosition("pair", 7, any()) }
        assertEquals(emptyList<UserProgressEntity>(), upserted())
    }

    @Test
    fun `a device already in the bad state is repaired without a newer bookmark`() = runTest {
        // A fresh install on the old build pulled the bookmark and dropped the
        // flag. Its bookmark now matches the server, so it is never pulled
        // again — the projection must still run on the tie.
        bulk(pairRow(completed = true))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns localBookmark(REMOTE_AT)
        localRows(ebook = null, audiobook = null)

        positions().syncAllBookmarksAndProgress(listOf(pair))

        coVerify(exactly = 0) { bookmarkDao.upsertBookmark(any()) }
        assertEquals(setOf("ebook" to 70, "audiobook" to 700),
            upserted().filter { it.isCompleted }.map { it.mediaType to it.mediaId }.toSet())
    }

    @Test
    fun `a matching local flag is not rewritten on every sync`() = runTest {
        bulk(pairRow(completed = true))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns localBookmark(REMOTE_AT)
        localRows(
            ebook = localProgress("ebook", 70, completed = true, at = OLDER_AT),
            audiobook = localProgress("audiobook", 700, completed = true, at = OLDER_AT),
        )

        positions().syncAllBookmarksAndProgress(listOf(pair))

        assertEquals(emptyList<UserProgressEntity>(), upserted())
    }

    @Test
    fun `the per-pair fallback projects completion too`() = runTest {
        coEvery { api.getPositions(any(), any()) } throws HttpException(
            Response.error<Any>(404, "".toResponseBody(null))
        )
        coEvery { api.getPosition("pair", 7) } returns Response.success(pairRow(completed = true))
        coEvery { api.getPosition("audiobook", 700) } returns Response.success<PositionResponse>(204, null)
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns null
        localRows(ebook = null, audiobook = null)

        positions().syncAllBookmarksAndProgress(listOf(pair))

        assertEquals(setOf("ebook" to 70, "audiobook" to 700),
            upserted().filter { it.isCompleted }.map { it.mediaType to it.mediaId }.toSet())
    }

    @Test
    fun `an unfinished pair with no local rows creates none`() = runTest {
        bulk(pairRow(completed = false))
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns null
        localRows(ebook = null, audiobook = null)

        positions().syncAllBookmarksAndProgress(listOf(pair))

        assertEquals(emptyList<UserProgressEntity>(), upserted())
    }

    private companion object {
        const val OLDER_AT = "2026-08-01T10:00:00Z"
        const val REMOTE_AT = "2026-08-02T10:00:00Z"
        const val NEWER_AT = "2026-08-03T10:00:00Z"
    }
}
