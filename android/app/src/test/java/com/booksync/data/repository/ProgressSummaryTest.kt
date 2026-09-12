package com.booksync.data.repository

import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Whether a book has a position worth resetting, and whether it is finished
 * (issue #484).
 *
 * The overflow menus offered "Reset progress" unconditionally and "Mark
 * complete" always, because neither fact reached them: `OverflowTarget` carried
 * no progress field at all and every call site passed `isComplete = false`
 * hardcoded. Both facts are in Room already — `bookmarks` for a pair,
 * `user_progress` for the per-format rows — so this is the decision, kept pure
 * and away from the Compose layer that consumes it.
 *
 * The subtle half is what counts as progress. The position-sync contract is
 * explicit that the start of a book is indistinguishable from an unread one
 * (`docs/position-sync-contract.md`; `isStartOfBook` and the "0% is not an
 * anchor" rule in the restore ladder), and background saves do write chapter 0
 * at 0 ms. Treating any stored row as progress would put the reset back on
 * books nobody has opened, which is the bug.
 */
class ProgressSummaryTest {

    private fun bookmark(
        chapter: Int? = null,
        sentence: Int? = null,
        audioMs: Int? = null,
    ) = BookmarkEntity(
        scopeKey = "s",
        bookPairId = 42,
        source = "ebook",
        epubChapter = chapter,
        epubSentenceIndex = sentence,
        audioPositionMs = audioMs,
        updatedAt = "2026-01-01T00:00:00",
    )

    private fun progress(
        percent: Float? = null,
        audioMs: Int? = null,
        chapter: Int? = null,
        completed: Boolean = false,
    ) = UserProgressEntity(
        scopeKey = "s",
        mediaType = "ebook",
        mediaId = 7,
        bookPairId = 42,
        epubCfi = null,
        epubChapter = chapter,
        epubProgressPercent = percent,
        audioPositionMs = audioMs,
        isCompleted = completed,
        updatedAt = 0L,
        deviceId = null,
    )

    // ---- nothing at all ----

    @Test
    fun `a book nobody has opened has nothing to reset`() {
        val summary = progressSummaryOf(bookmark = null, rows = emptyList())
        assertFalse(summary.hasProgress)
        assertFalse(summary.isComplete)
    }

    // ---- the start of the book is not progress ----

    @Test
    fun `a position at the very start is not progress`() {
        assertFalse(
            "chapter 0 at 0 ms is what a background save writes for an unopened book",
            progressSummaryOf(bookmark(chapter = 0, sentence = 0, audioMs = 0), emptyList()).hasProgress,
        )
    }

    @Test
    fun `zero percent is not progress`() {
        assertFalse(progressSummaryOf(null, listOf(progress(percent = 0f))).hasProgress)
    }

    // ---- real positions ----

    @Test
    fun `a chapter past the first is progress`() {
        assertTrue(progressSummaryOf(bookmark(chapter = 3), emptyList()).hasProgress)
    }

    @Test
    fun `a sentence past the first is progress, even in chapter zero`() {
        // A long first chapter is a real place to be.
        assertTrue(progressSummaryOf(bookmark(chapter = 0, sentence = 12), emptyList()).hasProgress)
    }

    @Test
    fun `an audio position is progress`() {
        assertTrue(progressSummaryOf(bookmark(audioMs = 45_000), emptyList()).hasProgress)
        assertTrue(progressSummaryOf(null, listOf(progress(audioMs = 45_000))).hasProgress)
    }

    @Test
    fun `a percentage above zero is progress`() {
        assertTrue(progressSummaryOf(null, listOf(progress(percent = 0.4f))).hasProgress)
    }

    /** The bookmark and the per-format rows are both consulted: either counts. */
    @Test
    fun `progress on either side of the pair counts`() {
        assertTrue(
            progressSummaryOf(bookmark(chapter = 0), listOf(progress(audioMs = 900_000))).hasProgress,
        )
    }

    // ---- completion ----

    @Test
    fun `a finished book reports complete, and has progress to reset`() {
        val summary = progressSummaryOf(null, listOf(progress(completed = true)))
        assertTrue(summary.isComplete)
        assertTrue(
            "finishing a book is the strongest progress there is — the reset must stay offered",
            summary.hasProgress,
        )
    }

    @Test
    fun `one finished format finishes the pair`() {
        // markPairComplete writes both rows, but a partial write must not read
        // as unfinished and re-offer "Mark complete" on a book already done.
        val summary = progressSummaryOf(
            null,
            listOf(progress(completed = true), progress(completed = false)),
        )
        assertTrue(summary.isComplete)
    }

    @Test
    fun `an unfinished book is not complete`() {
        assertFalse(progressSummaryOf(bookmark(chapter = 3), listOf(progress(percent = 30f))).isComplete)
    }
}
