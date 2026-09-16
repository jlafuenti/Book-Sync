package com.booksync.auto

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.lastPlayedAtMs
import com.booksync.player.MediaId

/**
 * Room rows → [AutoBook] browse rows (issue #225).
 *
 * The two entity shapes have different column names for the same facts; this
 * is the one place they are flattened, so the media id, the seconds→ms
 * duration conversion and which columns become the title and artist are
 * decided in plain Kotlin rather than inside `AudioPlayerService`, which is
 * excluded from Kover.
 *
 * The position record is a separate Room read — a bookmark for a pair, a
 * progress row for a standalone book — so the caller supplies it. It is passed
 * whole rather than pre-reduced to a resume position, because the browse row
 * needs its *timestamp* too: Continue Listening orders both kinds of book
 * together and the two rows spell that timestamp differently (issue #574).
 * `lastPlayedAtMs` is the same normalisation Home's Continue Reading uses.
 *
 * Both are plain functions so the service stays "read Room, hand the rows to
 * the pure code" — the same rule `AutoBrowseTree` set in issue #172.
 */

/** The audiobook's title and author, not the ebook's: this row is what the car reads aloud. */
fun BookPairEntity.toAutoBook(bookmark: BookmarkEntity?): AutoBook = AutoBook(
    mediaId = MediaId.Pair(id).value,
    title = audiobookTitle,
    author = audiobookAuthor,
    series = ebookSeries,
    audiobookId = audiobookId,
    pairId = id,
    resumePositionMs = bookmark?.audioPositionMs?.toLong() ?: 0L,
    durationMs = (audiobookDurationSeconds ?: 0) * 1000L,
    lastPlayedAtMs = bookmark?.lastPlayedAtMs() ?: 0L,
    audioFilename = audiobookFilename,
    serverCoverPath = audiobookCoverPath,
)

fun AudioBookEntity.toAutoBook(progress: UserProgressEntity?): AutoBook = AutoBook(
    mediaId = MediaId.Audiobook(id).value,
    title = title,
    author = author,
    series = series,
    audiobookId = id,
    resumePositionMs = progress?.audioPositionMs?.toLong() ?: 0L,
    durationMs = (durationSeconds ?: 0) * 1000L,
    lastPlayedAtMs = progress?.lastPlayedAtMs() ?: 0L,
    audioFilename = filename,
    serverCoverPath = coverFilename,
)
