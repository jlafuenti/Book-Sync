package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.BookmarkLogDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.BookmarkLogEntity
import com.booksync.data.local.entity.PendingSyncEntity
import com.booksync.data.local.entity.SyncPointEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.BookmarkLogResponse
import com.booksync.data.remote.PositionResponse
import com.booksync.data.remote.PositionUpdateRequest
import com.booksync.player.PlaybackOffsets
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Test
import retrofit2.Response
import java.io.IOException

/**
 * Direct tests for [PositionRepository] (issue #224), covering the moved code
 * that had no test of its own: the startup reconcile, bookmark history, the
 * sync-map position conversions, the locator-only write, and the 409 branch of
 * the offline drain. The behaviour is unchanged from when it lived in
 * `BookSyncRepository`; the tests pin it at its new home.
 */
class PositionRepositoryTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val bookmarkLogDao = mockk<BookmarkLogDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)
    private val syncPointDao = mockk<SyncPointDao>(relaxed = true)

    private fun positions() = buildPositionRepository(
        api = api,
        bookmarkDao = bookmarkDao,
        bookmarkLogDao = bookmarkLogDao,
        pendingSyncDao = pendingSyncDao,
        userProgressDao = userProgressDao,
        syncPointDao = syncPointDao,
    )

    private fun pair(id: Int = 7) = BookPairEntity(
        id = id, ebookId = 70, ebookTitle = "E", ebookAuthor = null, ebookFilename = "e.epub",
        ebookFormat = "epub", audiobookId = 700, audiobookTitle = "A", audiobookAuthor = null,
        audiobookFilename = "a.m4b", audiobookFormat = "m4b", audiobookDurationSeconds = null,
        status = "synced",
    )

    private fun serverPosition(chapter: Int, capturedAt: String) = PositionResponse(
        scope = "pair", book_pair_id = 7, source = "ebook", anchor_revision = 1,
        epub_chapter = chapter, epub_sentence_index = 3, audio_position_ms = 1_000,
        captured_at = capturedAt, updated_at = capturedAt,
    )

    private fun localBookmark(chapter: Int, capturedAt: String, synced: Boolean) = BookmarkEntity(
        scopeKey = TEST_SCOPE, bookPairId = 7, source = "ebook", epubChapter = chapter,
        epubSentenceIndex = 0, audioPositionMs = 0, updatedAt = "1000",
        capturedAt = capturedAt, syncedToServer = synced,
    )

    private fun point(chapter: Int, sentence: Int, audioMs: Int, preview: String?) = SyncPointEntity(
        bookPairId = 7, epubChapter = chapter, epubSentenceIndex = sentence,
        epubTextPreview = preview, audioStartMs = audioMs, audioEndMs = audioMs + 1_000,
    )

    // ---- Startup reconcile ------------------------------------------------

    @Test
    fun `startup reconcile pulls a server bookmark that is newer than the synced local one`() = runTest {
        coEvery { api.getPosition("pair", 7) } returns
            Response.success(serverPosition(chapter = 12, capturedAt = "2026-08-02T10:00:00Z"))
        coEvery { api.getPosition("audiobook", 700) } returns Response.success<PositionResponse>(204, null)
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 700) } returns null
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns
            localBookmark(chapter = 4, capturedAt = "2026-08-01T10:00:00Z", synced = true)

        positions().syncAllBookmarksAndProgress(listOf(pair()))

        val written = slot<BookmarkEntity>()
        coVerify(exactly = 1) { bookmarkDao.upsertBookmark(capture(written)) }
        assertEquals(12, written.captured.epubChapter)
        assertEquals(true, written.captured.syncedToServer)
        coVerify(exactly = 0) { api.updatePosition(any(), any(), any()) }
    }

    @Test
    fun `startup reconcile pushes an unsynced local bookmark even when the server has nothing`() = runTest {
        coEvery { api.getPosition("pair", 7) } returns Response.success<PositionResponse>(204, null)
        coEvery { api.getPosition("audiobook", 700) } returns Response.success<PositionResponse>(204, null)
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 700) } returns null
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 7) } returns
            localBookmark(chapter = 4, capturedAt = "2026-08-01T10:00:00Z", synced = false)
        coEvery { api.updatePosition("pair", 7, any()) } returns
            Response.success(serverPosition(chapter = 4, capturedAt = "2026-08-01T10:00:00Z"))

        positions().syncAllBookmarksAndProgress(listOf(pair()))

        val request = slot<PositionUpdateRequest>()
        coVerify(exactly = 1) { api.updatePosition("pair", 7, capture(request)) }
        assertEquals(4, request.captured.epub_chapter)
        assertEquals("2026-08-01T10:00:00Z", request.captured.captured_at)
        val written = slot<BookmarkEntity>()
        coVerify(exactly = 1) { bookmarkDao.upsertBookmark(capture(written)) }
        assertEquals(true, written.captured.syncedToServer)
    }

    // ---- Bookmark history ---------------------------------------------------

    @Test
    fun `bookmark history falls back to the local cache when the server is unreachable`() = runTest {
        coEvery { api.getBookmarkLog(7, 50) } throws IOException("offline")
        coEvery { bookmarkLogDao.getForPair(TEST_SCOPE, 7, 50) } returns listOf(
            BookmarkLogEntity(
                localId = 3, scopeKey = TEST_SCOPE, serverId = null, bookPairId = 7, source = "ebook",
                prevEpubChapter = 1, prevEpubSentenceIndex = 0, prevAudioPositionMs = 0,
                newEpubChapter = 2, newEpubSentenceIndex = 5, newAudioPositionMs = 9_000,
                changedAt = "2026-08-01T10:00:00",
            )
        )

        val history = positions().getBookmarkHistory(7)

        assertEquals(1, history.size)
        assertEquals(-3, history[0].id)
        assertEquals(2, history[0].new_epub_chapter)
        coVerify(exactly = 0) { bookmarkLogDao.insertLocal(any()) }
    }

    @Test
    fun `bookmark history caches server entries and prunes the local-only rows they supersede`() = runTest {
        coEvery { api.getBookmarkLog(7, 50) } returns listOf(
            BookmarkLogResponse(id = 11, source = "ebook", changed_at = "2026-08-01T10:00:00"),
            BookmarkLogResponse(id = 12, source = "audiobook", changed_at = "2026-08-02T10:00:00"),
        )
        coEvery { bookmarkLogDao.findByServerId(TEST_SCOPE, 7, 11) } returns 40L
        coEvery { bookmarkLogDao.findByServerId(TEST_SCOPE, 7, 12) } returns null

        positions().getBookmarkHistory(7)

        val updated = slot<BookmarkLogEntity>()
        coVerify(exactly = 1) { bookmarkLogDao.update(capture(updated)) }
        assertEquals(40L, updated.captured.localId)
        val inserted = slot<BookmarkLogEntity>()
        coVerify(exactly = 1) { bookmarkLogDao.insertLocal(capture(inserted)) }
        assertEquals(12, inserted.captured.serverId)
        coVerify(exactly = 1) { bookmarkLogDao.deleteLocalOnlyOlderThan(TEST_SCOPE, 7, "2026-08-02T10:00:00") }
        coVerify(exactly = 1) { bookmarkLogDao.getForPair(TEST_SCOPE, 7, 50) }
    }

    // ---- Position conversion -------------------------------------------------

    @Test
    fun `audioToEpubText names the first point for audio before the map and nothing for no map`() = runTest {
        coEvery { syncPointDao.getPointsForPair(7) } returns listOf(
            point(chapter = 3, sentence = 0, audioMs = 60_000, preview = "Chapter three opens"),
            point(chapter = 3, sentence = 1, audioMs = 65_000, preview = "and continues"),
        )
        val repo = positions()

        assertEquals(Pair(3, "Chapter three opens"), repo.audioToEpubText(7, 10_000))
        assertEquals(Pair(3, "and continues"), repo.audioToEpubText(7, 66_000))

        coEvery { syncPointDao.getPointsForPair(7) } returns emptyList()
        assertEquals(Pair(0, ""), repo.audioToEpubText(7, 10_000))
    }

    @Test
    fun `epubTextForSentence falls back to the nearest earlier sentence that has a preview`() = runTest {
        coEvery { syncPointDao.getPointsForPair(7) } returns listOf(
            point(chapter = 3, sentence = 0, audioMs = 60_000, preview = "first"),
            point(chapter = 3, sentence = 4, audioMs = 70_000, preview = null),
            point(chapter = 3, sentence = 8, audioMs = 80_000, preview = "ninth"),
            point(chapter = 4, sentence = 0, audioMs = 90_000, preview = "other chapter"),
        )
        val repo = positions()

        assertEquals("first", repo.epubTextForSentence(7, 3, 6))
        assertEquals("ninth", repo.epubTextForSentence(7, 3, 8))
        assertEquals("first", repo.epubTextForSentence(7, 3, null))
        assertEquals("", repo.epubTextForSentence(7, 9, 0))
    }

    @Test
    fun `getSentenceIndexFromProgression maps a fraction onto the chapter's points`() = runTest {
        coEvery { syncPointDao.getPointsForPair(7) } returns listOf(
            point(chapter = 3, sentence = 10, audioMs = 60_000, preview = "a"),
            point(chapter = 3, sentence = 20, audioMs = 61_000, preview = "b"),
            point(chapter = 3, sentence = 30, audioMs = 62_000, preview = "c"),
            point(chapter = 4, sentence = 0, audioMs = 90_000, preview = "d"),
        )
        val repo = positions()

        assertEquals(10, repo.getSentenceIndexFromProgression(7, 3, 0f))
        assertEquals(20, repo.getSentenceIndexFromProgression(7, 3, 0.5f))
        assertEquals(30, repo.getSentenceIndexFromProgression(7, 3, 1f))
        assertEquals(0, repo.getSentenceIndexFromProgression(7, 8, 0.5f))
    }

    @Test
    fun `epubToAudioText applies the resume rewind and never goes below zero`() = runTest {
        val text = "The lighthouse keeper counted the ships that passed in the long grey evening."
        coEvery { syncPointDao.getPointsForPair(7) } returns listOf(
            point(chapter = 2, sentence = 0, audioMs = 3_000, preview = text),
        )
        val repo = positions()

        assertEquals(0, repo.epubToAudioText(7, 2, text))
        assertEquals(1_000, repo.epubToAudioText(7, 2, text, rewindMs = 2_000))
        assertEquals(
            maxOf(0, 3_000 - PlaybackOffsets.RESUME_REWIND_MS.toInt()),
            repo.epubToAudioText(7, 2, text),
        )
        coEvery { syncPointDao.getPointsForPair(7) } returns emptyList()
        assertEquals(0, repo.epubToAudioText(7, 2, text))
    }

    // ---- Locator-only write ----------------------------------------------------

    @Test
    fun `updateBookmarkLocator uses the audio-aware write only when an audio position is given`() = runTest {
        val repo = positions()

        repo.updateBookmarkLocator(7, "{\"href\":\"a.xhtml\"}")
        repo.updateBookmarkLocator(7, "{\"href\":\"b.xhtml\"}", audioMs = 5_000)

        coVerify(exactly = 1) { bookmarkDao.updateLocator(TEST_SCOPE, 7, "{\"href\":\"a.xhtml\"}") }
        coVerify(exactly = 1) { bookmarkDao.updateLocatorWithAudio(TEST_SCOPE, 7, "{\"href\":\"b.xhtml\"}", 5_000) }
    }

    // ---- Offline drain: the 409 branch -----------------------------------------

    @Test
    fun `a queued write that lost a 409 is dropped and the server state adopted`() = runTest {
        val queued = PendingSyncEntity(
            id = 5, scopeKey = TEST_SCOPE, bookPairId = 7, source = "ebook",
            epubChapter = 4, epubSentenceIndex = 1, audioPositionMs = 100, createdAt = 1_700_000_000_000L,
        )
        coEvery { pendingSyncDao.getPendingForScope(TEST_SCOPE) } returns listOf(queued)
        coEvery { bookmarkDao.getUnsyncedBookmarks(TEST_SCOPE) } returns emptyList()
        coEvery { userProgressDao.getUnsyncedProgress(TEST_SCOPE) } returns emptyList()
        val serverBody = """{"scope":"pair","book_pair_id":7,"source":"audiobook","anchor_revision":3,""" +
            """"epub_chapter":12,"audio_position_ms":9000,"updated_at":"2026-08-02T10:00:00Z"}"""
        coEvery { api.updatePosition("pair", 7, any()) } returns
            Response.error(409, serverBody.toResponseBody("application/json".toMediaType()))

        positions().processPendingSync()

        val request = slot<PositionUpdateRequest>()
        coVerify(exactly = 1) { api.updatePosition("pair", 7, capture(request)) }
        assertEquals("2023-11-14T22:13:20Z", request.captured.captured_at)
        val adopted = slot<BookmarkEntity>()
        coVerify(exactly = 1) { bookmarkDao.upsertBookmark(capture(adopted)) }
        assertEquals(12, adopted.captured.epubChapter)
        assertEquals("audiobook", adopted.captured.source)
        coVerify(exactly = 1) { pendingSyncDao.delete(queued) }
    }
}
