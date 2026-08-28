package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PositionResponse
import com.booksync.data.remote.PositionUpdateRequest
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.markPairComplete] finishes a *pair* through the pair
 * scope — one `PUT /api/sync/position/pair/{id}` carrying `is_completed=true`
 * and nothing else (issue #56, docs/position-sync-contract.md § Completion).
 *
 * It used to be two standalone-scope PUTs (`ebook/{id}` + `audiobook/{id}`),
 * which flagged the two standalone records and left the pair's own canonical
 * record un-finished — `GET /position/pair/{id}` still said `is_completed:
 * false` for a book the phone had just finished, while the web (which writes
 * the pair scope) disagreed. The server projects one pair-scoped flag onto both
 * `user_progress` rows itself, so a single write is also the cheaper one.
 */
class MarkPairCompleteTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)
    private val deviceIdManager = mockk<DeviceIdManager>(relaxed = true).also {
        every { it.deviceId } returns "pixel-9"
        every { it.deviceName } returns "Jesse's Pixel"
    }

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = bookmarkDao,
        pendingSyncDao = pendingSyncDao,
        userProgressDao = userProgressDao,
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = deviceIdManager,
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun okResponse() = Response.success(
        PositionResponse(
            scope = "pair", book_pair_id = 7, source = "audiobook",
            anchor_revision = 1, is_completed = true, updated_at = "2026-08-16T10:00:00Z",
        )
    )

    @Test
    fun `finishing a pair is one pair-scoped PUT carrying only is_completed`() = runTest {
        val body = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition("pair", 7, capture(body)) } returns okResponse()

        repository().markPairComplete(pairId = 7, ebookId = 100, audiobookId = 200)

        coVerify(exactly = 1) { api.updatePosition("pair", 7, any()) }
        coVerify(exactly = 0) { api.updatePosition("ebook", any(), any()) }
        coVerify(exactly = 0) { api.updatePosition("audiobook", any(), any()) }

        val sent = body.captured
        assertEquals(true, sent.is_completed)
        // A completion toggle carries no anchor and claims no format — the
        // server keeps whatever it has (contract: "a write carrying no anchor
        // never clears one"; background writes omit `source`).
        assertNull(sent.source)
        assertNull(sent.epub_chapter)
        assertNull(sent.epub_sentence_index)
        assertNull(sent.epub_progress_percent)
        assertNull(sent.audio_position_ms)
        assertEquals("pixel-9", sent.device_id)
        assertEquals("Jesse's Pixel", sent.device_name)
    }

    @Test
    fun `both local user_progress rows are flagged, keyed to the pair`() = runTest {
        coEvery { api.updatePosition("pair", 7, any()) } returns okResponse()
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "ebook", 100) } returns UserProgressEntity(scopeKey = TEST_SCOPE, 
            mediaType = "ebook", mediaId = 100, bookPairId = 7, epubCfi = null,
            epubChapter = 12, epubProgressPercent = 40f, audioPositionMs = null,
            isCompleted = false, updatedAt = 1L, deviceId = "pixel-9",
        )
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 200) } returns null

        val rows = mutableListOf<UserProgressEntity>()
        coEvery { userProgressDao.upsertProgress(capture(rows)) } returns Unit

        repository().markPairComplete(pairId = 7, ebookId = 100, audiobookId = 200)

        val byMedia = rows.associateBy { it.mediaType }
        assertEquals(setOf("ebook", "audiobook"), byMedia.keys)
        assertTrue(byMedia.getValue("ebook").isCompleted)
        assertTrue(byMedia.getValue("audiobook").isCompleted)
        assertEquals(7, byMedia.getValue("ebook").bookPairId)
        assertEquals(7, byMedia.getValue("audiobook").bookPairId)
        // Merging, not replacing: the ebook row keeps the position it had.
        assertEquals(12, byMedia.getValue("ebook").epubChapter)
        assertEquals(40f, byMedia.getValue("ebook").epubProgressPercent)
        assertTrue(byMedia.values.all { it.syncedToServer })
    }

    @Test
    fun `offline keeps the local rows flagged but unsynced so the sweep delivers them`() = runTest {
        coEvery { api.updatePosition("pair", 7, any()) } throws java.io.IOException("offline")
        val rows = mutableListOf<UserProgressEntity>()
        coEvery { userProgressDao.upsertProgress(capture(rows)) } returns Unit

        repository().markPairComplete(pairId = 7, ebookId = 100, audiobookId = 200) // must not throw

        assertEquals(2, rows.size)
        assertTrue(rows.all { it.isCompleted })
        assertTrue(rows.none { it.syncedToServer })
    }
}
