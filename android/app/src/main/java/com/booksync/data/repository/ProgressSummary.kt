package com.booksync.data.repository

import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity

/**
 * What the stored position rows say about a book (issue #484).
 *
 * The two facts the overflow menus and the details screen need in order to stop
 * offering actions with nothing to act on: a "Reset progress" row on a book
 * nobody has opened, and a "Mark complete" row on a book already finished.
 */
data class ProgressSummary(
    val hasProgress: Boolean,
    val isComplete: Boolean,
)

/**
 * Read [bookmark] and the per-format [rows] as one answer.
 *
 * **The start of a book is not progress.** `docs/position-sync-contract.md` is
 * explicit that chapter 0 at 0 ms is indistinguishable from an unread book —
 * it is what a background save writes before anyone has turned a page, and the
 * restore ladder refuses to treat 0% as an anchor for the same reason. Counting
 * any stored row as progress would put the reset straight back onto books
 * nobody has opened, which is the defect this exists to fix.
 *
 * A finished book always has progress: there is a position to clear, and the
 * reset is how you start it again.
 */
fun progressSummaryOf(
    bookmark: BookmarkEntity?,
    rows: List<UserProgressEntity?>,
): ProgressSummary {
    val present = rows.filterNotNull()
    val complete = present.any { it.isCompleted }

    val bookmarkMoved = bookmark != null && (
        (bookmark.epubChapter ?: 0) > 0 ||
            (bookmark.epubSentenceIndex ?: 0) > 0 ||
            (bookmark.audioPositionMs ?: 0) > 0
        )

    val rowMoved = present.any {
        (it.epubChapter ?: 0) > 0 ||
            (it.epubProgressPercent ?: 0f) > 0f ||
            (it.audioPositionMs ?: 0) > 0 ||
            it.epubCfi != null
    }

    return ProgressSummary(
        hasProgress = complete || bookmarkMoved || rowMoved,
        isComplete = complete,
    )
}
