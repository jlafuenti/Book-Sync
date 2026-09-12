package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * The order of Home's "Continue Reading" row (issue #476).
 *
 * `BookmarkEntity.updatedAt` is a `String` holding one of two shapes depending on
 * who wrote the row: epoch millis from an on-device save (`1788220800000`), or
 * ISO-8601 from a server response (`2026-09-10T12:00:00`). `HomeViewModel` parsed
 * it with `toLongOrNull()`, which only understands the first — so every bookmark
 * that arrived from the server scored **0** and sank to the bottom of the one list
 * whose job is to surface what you were last doing. That is the normal case for
 * anything read on the web, on another device, or before a reinstall.
 *
 * Nothing new is being invented here. `LibraryRepository.lastOpenedTimesFlow`
 * has always read this column through `parseSyncTimestamp(preferCapturedAt(...))`,
 * which is why the Library's "Recently opened" sort was right while Home's was
 * not. No test touched `updatedAtMs` at all, which is how the two diverged.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ContinueReadingOrderTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)

    // Exact instants, so every comparison below is between known values.
    private val sep01Millis = "1788220800000"      // 2026-09-01T00:00:00Z, local shape
    private val sep05Millis = 1788566400000L       // 2026-09-05T00:00:00Z
    private val sep10Iso = "2026-09-10T12:00:00"   // server shape, no zone → UTC

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getRecentlyPlayedStandaloneAudiobooksFlow() } returns flowOf(emptyList())
        every { repository.getRecentlyReadEbooksFlow() } returns flowOf(emptyList())
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun pair(id: Int) = BookPairEntity(
        id = id,
        ebookId = id, ebookTitle = "E$id", ebookAuthor = null, ebookFilename = "e$id.epub",
        ebookFormat = "epub",
        audiobookId = id, audiobookTitle = "A$id", audiobookAuthor = null,
        audiobookFilename = "a$id.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 3600,
        status = "synced",
    )

    private fun bookmark(pairId: Int, updatedAt: String, capturedAt: String? = null) = BookmarkEntity(
        scopeKey = "s",
        bookPairId = pairId,
        source = "audiobook",
        epubChapter = null,
        epubSentenceIndex = null,
        audioPositionMs = 60_000,
        updatedAt = updatedAt,
        capturedAt = capturedAt,
    )

    private fun newViewModel(): HomeViewModel {
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns emptyFlow()
        val networkMonitor = mockk<NetworkMonitor>(relaxed = true)
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        return HomeViewModel(
            repository = repository,
            transcriptionRepository = transcriptionRepository,
            networkMonitor = networkMonitor,
            serverUrlManager = serverUrlManager,
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(
                mockk(relaxed = true),
                serverUrlManager,
            ),
            context = mockk(relaxed = true),
        )
    }

    private fun HomeViewModel.order(): List<String> = continueItems.value.map { it.id }

    /**
     * The issue's own reproduction, and the test that has to be red before the fix.
     * A book read on the web ten days in sits *below* one touched locally on the
     * first, because its ISO timestamp parsed to nothing.
     */
    @Test
    fun `a book last read on another device sorts above an older local one`() {
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(listOf(pair(1), pair(2)))
        coEvery { repository.getBookmark(1) } returns bookmark(1, updatedAt = sep01Millis)
        coEvery { repository.getBookmark(2) } returns bookmark(2, updatedAt = sep10Iso)

        assertEquals(listOf("pair_2", "pair_1"), newViewModel().order())
    }

    /**
     * `capturedAt` is when the position was actually recorded on the device that
     * wrote it; `updatedAt` is when the row was last touched, which a sync pull
     * refreshes. Ordering by the wrong one would put a book at the top merely
     * because it was re-downloaded — and would disagree with the Library's
     * "Recently opened", which already prefers `capturedAt`.
     */
    @Test
    fun `the capture time beats the row-modified time`() {
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(listOf(pair(1), pair(2)))
        // Both `updatedAt` values are epoch millis on purpose. A first draft used
        // an ISO `updatedAt` here and passed *before* the fix — the ISO row scored
        // 0 under the bug, which happened to produce the expected order without
        // `capturedAt` mattering at all. With both parseable, ordering by
        // `updatedAt` and ordering by `capturedAt` give opposite answers, so only
        // genuinely preferring the capture time can pass.
        //
        // Pair 1: row touched on the 5th, position captured on the 1st.
        coEvery { repository.getBookmark(1) } returns
            bookmark(1, updatedAt = sep05Millis.toString(), capturedAt = "2026-09-01T00:00:00Z")
        // Pair 2: row touched on the 1st, position captured on the 10th.
        coEvery { repository.getBookmark(2) } returns
            bookmark(2, updatedAt = sep01Millis, capturedAt = "2026-09-10T12:00:00Z")

        assertEquals(listOf("pair_2", "pair_1"), newViewModel().order())
    }

    /** No usable time is a real state (a partial sync); it must sort last, not throw. */
    @Test
    fun `a missing or unreadable timestamp sorts last`() {
        every { repository.getRecentlyPlayedPairsFlow() } returns
            flowOf(listOf(pair(1), pair(2), pair(3)))
        coEvery { repository.getBookmark(1) } returns bookmark(1, updatedAt = "not a time")
        coEvery { repository.getBookmark(2) } returns bookmark(2, updatedAt = sep10Iso)
        coEvery { repository.getBookmark(3) } returns null

        assertEquals("pair_2", newViewModel().order().first())
    }

    /**
     * The case the bug made invisible. Standalone books read `UserProgressEntity`,
     * whose `updatedAt` the server mapper already stores as epoch millis — so
     * they were always ordered correctly, and a server-synced *pair* sank below
     * them too. The two halves of the row were on different footings.
     */
    @Test
    fun `a pair and a standalone audiobook interleave by real time`() {
        val audiobook = AudioBookEntity(
            id = 50, title = "Standalone", author = null, filename = "s.m4b",
            durationSeconds = 3600, format = "m4b", series = null, seriesIndex = null,
            uploadedAt = "2026-01-01T00:00:00", isDownloaded = true,
        )
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(listOf(pair(1)))
        every { repository.getRecentlyPlayedStandaloneAudiobooksFlow() } returns flowOf(listOf(audiobook))
        // The pair was listened to on the web on the 10th; the audiobook on the 5th.
        coEvery { repository.getBookmark(1) } returns bookmark(1, updatedAt = sep10Iso)
        coEvery { repository.getProgressOnce("audiobook", 50) } returns UserProgressEntity(
            scopeKey = "s", mediaType = "audiobook", mediaId = 50, bookPairId = null,
            epubCfi = null, epubChapter = null, epubProgressPercent = null,
            audioPositionMs = 30_000, isCompleted = false,
            updatedAt = sep05Millis, deviceId = null,
        )

        assertEquals(listOf("pair_1", "audiobook_50"), newViewModel().order())
    }
}
