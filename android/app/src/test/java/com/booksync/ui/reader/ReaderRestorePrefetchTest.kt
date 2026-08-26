package com.booksync.ui.reader

import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.PositionFetch
import com.booksync.data.remote.PositionResponse
import io.mockk.coEvery
import io.mockk.mockk
import java.io.File
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The reader's pre-restore server pull must be BOUNDED (issue #167): the
 * player and Android Auto wrap the same three calls in
 * withTimeoutOrNull(SERVER_POSITION_TIMEOUT_MS) and fall back to the local
 * cache; the reader awaited them bare, so a network that accepts connections
 * but never answers (captive portal, SYN black-hole) stalled a *downloaded*
 * book's open for up to RetryInterceptor × read-timeout — minutes — with no
 * cancel. The book is on the device; opening it needs no network.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ReaderRestorePrefetchTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Test
    fun `returns within the timeout when every server call hangs`() = runTest {
        coEvery { repository.refreshBookmark(42) } coAnswers { awaitCancellation() }
        coEvery { repository.ensureSyncMapCached(42) } coAnswers { awaitCancellation() }
        coEvery { repository.fetchPosition("pair", 42) } coAnswers { awaitCancellation() }

        // runTest fails the test if this suspends past virtual time — an
        // unbounded implementation hangs here instead of returning.
        val fetch = prefetchBeforeRestore(repository, pairId = 42, timeoutMs = 1_500L)

        assertFalse(
            "a timed-out pull reports unreachable so the caller falls back to the local row",
            fetch.reachable,
        )
        assertEquals(null, fetch.position)
    }

    @Test
    fun `passes the canonical record through when the server answers in time`() = runTest {
        val remote = PositionResponse(
            scope = "pair", book_pair_id = 42, source = "ebook",
            anchor_revision = 3, epub_chapter = 7, updated_at = "2026-08-20T10:00:00Z",
        )
        coEvery { repository.fetchPosition("pair", 42) } returns PositionFetch(remote, reachable = true)

        val fetch = prefetchBeforeRestore(repository, pairId = 42, timeoutMs = 1_500L)

        assertTrue(fetch.reachable)
        assertEquals(remote, fetch.position)
    }

    @Test
    fun `the reader has no bare unbounded pull left`() {
        // Source guard, SyncWiringTest style: ReaderActivity must route its
        // pre-restore pull through prefetchBeforeRestore — a bare
        // repository.refreshBookmark/fetchPosition call is the unbounded bug.
        var dir = File("").absoluteFile
        var text: String? = null
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/com/booksync/ui/reader/ReaderActivity.kt")
            if (candidate.exists()) { text = candidate.readText(); return@repeat }
            dir = dir.parentFile ?: return@repeat
        }
        val code = (text ?: throw AssertionError("ReaderActivity.kt not found"))
            .lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

        assertTrue(
            "ReaderActivity must call prefetchBeforeRestore(...)",
            code.any { it.contains("prefetchBeforeRestore(") },
        )
        assertTrue(
            "no bare repository.refreshBookmark / ensureSyncMapCached / fetchPosition " +
                "calls may remain in ReaderActivity (issue #167)",
            code.none {
                it.contains("repository.refreshBookmark(") ||
                    it.contains("repository.ensureSyncMapCached(") ||
                    it.contains("repository.fetchPosition(")
            },
        )
    }
}
