package com.booksync.auto

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.player.MediaId

/**
 * Room rows → [AutoBook] browse rows (issue #225).
 *
 * The two entity shapes have different column names for the same facts; this
 * is the one place they are flattened, so the media id, the seconds→ms
 * duration conversion and which columns become the title and artist are
 * decided in plain Kotlin rather than inside `AudioPlayerService`, which is
 * excluded from Kover. The resume position is a separate Room read (bookmark
 * for a pair, progress for a standalone book), so the caller supplies it.
 *
 * Both are plain functions so the service stays "read Room, hand the rows to
 * the pure code" — the same rule `AutoBrowseTree` set in issue #172.
 */

/** The audiobook's title and author, not the ebook's: this row is what the car reads aloud. */
fun BookPairEntity.toAutoBook(resumePositionMs: Long): AutoBook = AutoBook(
    mediaId = MediaId.Pair(id).value,
    title = audiobookTitle,
    author = audiobookAuthor,
    series = ebookSeries,
    audiobookId = audiobookId,
    pairId = id,
    resumePositionMs = resumePositionMs,
    durationMs = (audiobookDurationSeconds ?: 0) * 1000L,
    audioFilename = audiobookFilename,
    serverCoverPath = audiobookCoverPath,
)

fun AudioBookEntity.toAutoBook(resumePositionMs: Long): AutoBook = AutoBook(
    mediaId = MediaId.Audiobook(id).value,
    title = title,
    author = author,
    series = series,
    audiobookId = id,
    resumePositionMs = resumePositionMs,
    durationMs = (durationSeconds ?: 0) * 1000L,
    audioFilename = filename,
    serverCoverPath = coverFilename,
)
