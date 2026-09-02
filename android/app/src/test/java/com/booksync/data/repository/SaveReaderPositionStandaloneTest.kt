package com.booksync.data.repository

import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.BookmarkLogDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PositionResponse
import com.booksync.data.remote.PositionUpdateRequest
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.coVerifyOrder
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import retrofit2.Response

/**
 * Saving a reading position for a *standalone* (unpaired) ebook — issue #169.
 *
 * The paired reader saves through [BookSyncRepository.saveReaderPosition] →
 * `updateBookmark`, which writes a bookmark row, a bookmark-log entry and, on
 * failure, a `pending_sync` row. All three are keyed by `book_pair_id`, so a
 * standalone ebook cannot use that path — it would either write a null pair id
 * or attribute the position to some other book.
 *
 * This save mirrors [BookSyncRepository.savePlaybackPositionStandalone] instead:
 * Room projection first, then the one canonical endpoint under the `ebook`
 * scope. `bookmarks` is still the server's canonical record — it is scoped by
 * `ebook_id` there (docs/position-sync-contract.md, "One record") — the client
 * simply has no pair-keyed local mirror of it.
 *
 * The `source` rule is unchanged and load-bearing: "Reader saves always claim
 * `ebook`: having the reader open is consumption" (contract, "Who may claim
 * `source`").
 */
class SaveReaderPositionStandaloneTest {

    private val api = mockk<BookSyncApi>()
    private val bookmarkDao = mockk<BookmarkDao>(relaxed = true)
    private val bookmarkLogDao = mockk<BookmarkLogDao>(relaxed = true)
    private val pendingSyncDao = mockk<PendingSyncDao>(relaxed = true)
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)

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
        bookmarkLogDao = bookmarkLogDao,
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun okResponse() = PositionResponse(
        scope = "ebook",
        ebook_id = 7,
        source = "ebook",
        anchor_revision = 3L,
        updated_at = "2026-09-01T00:00:00Z",
    )

    @Test
    fun `the save goes to the ebook scope with the ebook id`() = runTest {
        val scope = slot<String>()
        val id = slot<Int>()
        coEvery { api.updatePosition(capture(scope), capture(id), any()) } returns
            Response.success(okResponse())

        repository().saveReaderPositionStandalone(
            ebookId = 7,
            epubChapter = 4,
            epubSentenceIndex = 11,
        )

        assertEquals("ebook", scope.captured)
        assertEquals(7, id.captured)
    }

    @Test
    fun `the reader claims the ebook format, because having the reader open is consumption`() =
        runTest {
            val body = slot<PositionUpdateRequest>()
            coEvery { api.updatePosition(any(), any(), capture(body)) } returns
                Response.success(okResponse())

            repository().saveReaderPositionStandalone(ebookId = 7, epubChapter = 4)

            assertEquals("ebook", body.captured.source)
        }

    @Test
    fun `the whole position goes out, not just the chapter`() = runTest {
        // Contract, "The write gate": full position per write — anchors are never
        // inherited from a stale row, and omission on the wire means "leave
        // alone". A save that dropped the sentence index would silently coarsen
        // the position every time the reader saved.
        val body = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition(any(), any(), capture(body)) } returns
            Response.success(okResponse())

        repository().saveReaderPositionStandalone(
            ebookId = 7,
            epubChapter = 4,
            epubSentenceIndex = 11,
            epubTextPreview = "It was a pleasure to burn.",
            epubProgressPercent = 0.42f,
        )

        assertEquals(4, body.captured.epub_chapter)
        assertEquals(11, body.captured.epub_sentence_index)
        assertEquals("It was a pleasure to burn.", body.captured.epub_text_preview)
        assertEquals(0.42f, body.captured.epub_progress_percent)
    }

    @Test
    fun `the write carries device attribution and a capture time`() = runTest {
        // Multi-device conflict resolution needs both (issue #54); a write
        // without them cannot be ordered against another device's.
        val body = slot<PositionUpdateRequest>()
        coEvery { api.updatePosition(any(), any(), capture(body)) } returns
            Response.success(okResponse())

        repository().saveReaderPositionStandalone(ebookId = 7, epubChapter = 4)

        assertNotNull("captured_at must be sent", body.captured.captured_at)
        assertTrue("captured_at must not be blank", body.captured.captured_at!!.isNotBlank())
        assertNotNull("device_id must be sent", body.captured.device_id)
    }

    @Test
    fun `it never touches the pair-keyed bookmark tables`() = runTest {
        // The whole reason this is a separate method. bookmarks, bookmark_log
        // and pending_sync are all keyed by book_pair_id; writing a standalone
        // position through them attributes it to a pair that does not exist.
        coEvery { api.updatePosition(any(), any(), any()) } returns Response.success(okResponse())

        repository().saveReaderPositionStandalone(ebookId = 7, epubChapter = 4)

        coVerify(exactly = 0) { bookmarkDao.upsertBookmark(any()) }
        coVerify(exactly = 0) { bookmarkLogDao.insertLocal(any()) }
        coVerify(exactly = 0) { pendingSyncDao.insert(any()) }
    }

    @Test
    fun `the local row is written before the server is asked`() = runTest {
        // Room-first (issue #164), the same ordering savePlaybackPositionStandalone
        // uses: a save that only reached the server is lost if the process dies
        // before the projection is written, and the reader then reopens at the
        // old page.
        coEvery { api.updatePosition(any(), any(), any()) } returns Response.success(okResponse())

        repository().saveReaderPositionStandalone(ebookId = 7, epubChapter = 4)

        coVerifyOrder {
            userProgressDao.upsertProgress(any())
            api.updatePosition(any(), any(), any())
        }
    }

    // ------------------------------------------------------------------
    // Wiring. Everything above passes if saveReaderPositionStandalone is
    // never called by anything: a correct, well-tested, dead method, and a
    // standalone reader that silently persists nothing. Four guards in this
    // repository have already shipped inert, so read the reader.
    // ------------------------------------------------------------------

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath")
    }

    @Test
    fun `the reader actually saves through the standalone path`() {
        assertTrue(
            "ReaderActivity must call saveReaderPositionStandaloneDetached; " +
                "without it a standalone ebook reopens at page one forever and " +
                "every test above is asserting dead code.",
            source("com/booksync/ui/reader/ReaderActivity.kt")
                .contains("saveReaderPositionStandaloneDetached"),
        )
    }

    @Test
    fun `the reader refreshes the server position before restoring`() {
        // Contract, "The write gate": "Resume paths refresh first". Falling
        // through to the paired prefetch would query a pair id of zero and
        // quietly restore from nothing.
        assertTrue(
            "ReaderActivity must use prefetchBeforeRestoreStandalone for an unpaired ebook.",
            source("com/booksync/ui/reader/ReaderActivity.kt")
                .contains("prefetchBeforeRestoreStandalone"),
        )
    }

    @Test
    fun `no entry point still dead-ends on a standalone ebook`() {
        // Home and Downloaded both had `onOpenEbook = { }` / `onEbookSelect = { }`
        // no-ops: the tap was accepted and nothing happened (issue #169).
        val nav = source("com/booksync/ui/BookSyncNavigation.kt")
        assertFalse(
            "a standalone ebook tap must navigate somewhere, not into an empty lambda",
            nav.contains("no pair-based reader support yet"),
        )
        assertTrue(
            "Home and Downloaded must route a standalone ebook to the reader",
            Regex("""readerStandalone\(""").findAll(nav).count() >= 2,
        )
    }
}
