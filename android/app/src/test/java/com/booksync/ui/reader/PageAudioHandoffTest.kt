package com.booksync.ui.reader

import com.booksync.SyncState
import com.booksync.data.repository.BookSyncRepository
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The reader's page→audio handoff (issue #114).
 *
 * The toolbar's speaker button used to call `switchToAudio()`, which saved the
 * ebook locator and let the player resolve audio from the portable chapter
 * anchor — close, but not the sentence on screen. The precise path
 * (`syncAudioToPage`, DOM text → sync-point match) existed and had no callers at
 * all, so #113's rewind change was half dead on arrival.
 *
 * Now the button runs the precise path, and this pins what a match must write.
 * Two of those writes are easy to leave out and silent when missing:
 * `SyncState.pendingAudioSeekMs`, which is how the player seeks without waiting
 * for a server round-trip, and `locatorAudioMs`, which is what lets a return to
 * the reader within ~30s land on the same page instead of re-deriving one.
 */
class PageAudioHandoffTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun clearPendingSeek() { SyncState.pendingAudioSeekMs = -1L }

    @After
    fun resetPendingSeek() { SyncState.pendingAudioSeekMs = -1L }

    @Test
    fun `a match writes the bookmark and publishes the seek`() = runTest {
        val applied = PageAudioHandoff.apply(
            repository = repository,
            pairId = 84,
            chapterIndex = 16,
            locatorJson = """{"href":"/ch16.xhtml"}""",
            audioMs = 1_234_000,
        )

        assertTrue(applied)
        assertEquals(1_234_000L, SyncState.pendingAudioSeekMs)
        coVerify(exactly = 1) {
            repository.updateBookmark(
                pairId = 84,
                source = "ebook",
                epubChapter = 16,
                audioPositionMs = 1_234_000,
                epubLocator = """{"href":"/ch16.xhtml"}""",
                locatorAudioMs = 1_234_000,
            )
        }
    }

    @Test
    fun `no match writes nothing and leaves the player alone`() = runTest {
        val applied = PageAudioHandoff.apply(
            repository = repository,
            pairId = 84,
            chapterIndex = 16,
            locatorJson = """{"href":"/ch16.xhtml"}""",
            audioMs = 0,
        )

        assertFalse(applied)
        assertEquals(-1L, SyncState.pendingAudioSeekMs)
        coVerify(exactly = 0) { repository.updateBookmark(any(), any(), any(), any(), any(), any(), any(), any(), any(), any()) }
    }

    @Test
    fun `a negative match is treated as no match`() = runTest {
        assertFalse(
            PageAudioHandoff.apply(repository, pairId = 1, chapterIndex = 0, locatorJson = null, audioMs = -1)
        )
        assertEquals(-1L, SyncState.pendingAudioSeekMs)
    }
}
