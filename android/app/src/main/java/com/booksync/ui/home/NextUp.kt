package com.booksync.ui.home

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.lastPlayedAtMs

/**
 * "Next up" (issue #716): for each series you are reading, the next book in it.
 *
 * One rule with the web (`web/src/lib/nextUp.js`); both are held to
 * `server/tests/fixtures/sync_parity/next_up_cases.json` by [NextUpParityTest].
 *
 * - A **book** is one openable thing: a pair counts once (`pair_N`), an unpaired
 *   ebook or audiobook is its own (`ebook_N` / `audiobook_N`).
 * - Books group by series name, ignoring case and outer spaces.
 * - A series is **active** when any of its books was started or finished within
 *   [NEXT_UP_WINDOW_DAYS]. Measured on a real library, an untimed rule listed 26
 *   series for one reader, most of them books opened long ago.
 * - The **next** book is the lowest-numbered one above the furthest-numbered book
 *   touched, and not touched itself. Fractional numbers (novellas) count. A number
 *   the library lacks is skipped to the next book it has. Ties at one number
 *   prefer a pair, then an ebook.
 * - Rows come out most recently active first, like Continue Reading.
 */
const val NEXT_UP_WINDOW_DAYS = 90L

private const val DAY_MS = 24L * 60 * 60 * 1000

data class NextUpBook(
    val key: String,
    val series: String?,
    val index: Double?,
    val title: String = "",
    val author: String? = null,
    val pairId: Int? = null,
    val ebookId: Int? = null,
    val audiobookId: Int? = null,
    val ebookCoverPath: String? = null,
    val audiobookCoverPath: String? = null,
)

data class NextUpActivity(val key: String, val completed: Boolean, val atMs: Long)

data class NextUpRow(val series: String, val book: NextUpBook, val lastActivityMs: Long)

private fun normalizeSeries(name: String?) = name?.trim()?.lowercase().orEmpty()

private fun tieRank(key: String): Pair<Int, Int> {
    val kind = key.substringBefore('_')
    val id = key.substringAfter('_').toIntOrNull() ?: 0
    val rank = when (kind) { "pair" -> 0; "ebook" -> 1; "audiobook" -> 2; else -> 3 }
    return rank to id
}

fun computeNextUp(books: List<NextUpBook>, activity: List<NextUpActivity>, nowMs: Long): List<NextUpRow> {
    val lastByKey = HashMap<String, Long>()
    for (a in activity) {
        if (a.atMs <= 0L) continue
        lastByKey[a.key] = maxOf(lastByKey[a.key] ?: Long.MIN_VALUE, a.atMs)
    }

    val cutoff = nowMs - NEXT_UP_WINDOW_DAYS * DAY_MS
    val rows = mutableListOf<NextUpRow>()
    for (members in books.filter { normalizeSeries(it.series).isNotEmpty() }
        .groupBy { normalizeSeries(it.series) }.values
    ) {
        val touched = members.filter { it.key in lastByKey }
        if (touched.isEmpty()) continue

        val lastActivity = touched.maxOf { lastByKey.getValue(it.key) }
        if (lastActivity < cutoff) continue

        val frontier = touched.mapNotNull { it.index }.maxOrNull() ?: continue

        val next = members
            .filter { it.index != null && it.index > frontier && it.key !in lastByKey }
            .sortedWith(
                compareBy<NextUpBook> { it.index }
                    .thenBy { tieRank(it.key).first }
                    .thenBy { tieRank(it.key).second },
            )
            .firstOrNull() ?: continue

        rows += NextUpRow(series = next.series.orEmpty(), book = next, lastActivityMs = lastActivity)
    }
    return rows.sortedByDescending { it.lastActivityMs }
}

/**
 * The Room library, reduced to [computeNextUp]'s inputs.
 *
 * A pair becomes one book, taking its series and number from the ebook and
 * falling back to the audiobook. Progress counts as activity only when it is
 * real: finished, or with some position; a book merely opened at 0 % is not
 * being read. Its time is the capture time, falling back to `updatedAt`
 * ([lastPlayedAtMs]), since `updatedAt` also moves on server-side rewrites such
 * as a realign (issue #679). Progress on either half of a pair, and the pair's
 * own bookmark (fresher while the phone is offline), are filed under the pair.
 */
fun libraryToNextUpInput(
    pairs: List<BookPairEntity>,
    ebooks: List<EBookEntity>,
    audiobooks: List<AudioBookEntity>,
    progress: List<UserProgressEntity>,
    bookmarks: List<BookmarkEntity>,
): Pair<List<NextUpBook>, List<NextUpActivity>> {
    val ebookById = ebooks.associateBy { it.id }
    val audiobookById = audiobooks.associateBy { it.id }
    val pairByEbook = pairs.associateBy { it.ebookId }
    val pairByAudiobook = pairs.associateBy { it.audiobookId }

    val books = mutableListOf<NextUpBook>()
    for (p in pairs) {
        val ab = audiobookById[p.audiobookId]
        val series = p.ebookSeries?.takeIf { it.isNotBlank() } ?: ab?.series
        val index = p.ebookSeriesIndex ?: ab?.seriesIndex
        books += NextUpBook(
            key = "pair_${p.id}", series = series, index = index?.toDouble(),
            title = p.ebookTitle ?: p.audiobookTitle, author = p.ebookAuthor ?: p.audiobookAuthor,
            pairId = p.id, ebookId = p.ebookId, audiobookId = p.audiobookId,
            ebookCoverPath = ebookById[p.ebookId]?.coverFilename,
            audiobookCoverPath = p.audiobookCoverPath,
        )
    }
    for (eb in ebooks) {
        if (eb.id in pairByEbook) continue
        books += NextUpBook(
            key = "ebook_${eb.id}", series = eb.series, index = eb.seriesIndex?.toDouble(),
            title = eb.title, author = eb.author, ebookId = eb.id, ebookCoverPath = eb.coverFilename,
        )
    }
    for (ab in audiobooks) {
        if (ab.id in pairByAudiobook) continue
        books += NextUpBook(
            key = "audiobook_${ab.id}", series = ab.series, index = ab.seriesIndex?.toDouble(),
            title = ab.title, author = ab.author, audiobookId = ab.id, audiobookCoverPath = ab.coverFilename,
        )
    }

    val activity = mutableListOf<NextUpActivity>()
    for (row in progress) {
        val real = row.isCompleted ||
            (row.mediaType == "ebook" && ((row.epubProgressPercent ?: 0f) > 0f || (row.epubChapter ?: 0) > 0)) ||
            (row.mediaType == "audiobook" && (row.audioPositionMs ?: 0) > 0)
        if (!real) continue
        val key = when {
            row.bookPairId != null -> "pair_${row.bookPairId}"
            row.mediaType == "ebook" -> pairByEbook[row.mediaId]?.let { "pair_${it.id}" } ?: "ebook_${row.mediaId}"
            row.mediaType == "audiobook" -> pairByAudiobook[row.mediaId]?.let { "pair_${it.id}" } ?: "audiobook_${row.mediaId}"
            else -> continue
        }
        activity += NextUpActivity(key, row.isCompleted, row.lastPlayedAtMs())
    }
    for (bm in bookmarks) {
        val real = (bm.audioPositionMs ?: 0) > 0 || (bm.epubChapter ?: 0) > 0 || (bm.epubSentenceIndex ?: 0) > 0
        if (!real) continue
        activity += NextUpActivity("pair_${bm.bookPairId}", completed = false, atMs = bm.lastPlayedAtMs())
    }
    return books to activity
}

/** A Next up row as the home screen's card model; the card opens its details. */
internal fun NextUpRow.toHomeItem(): HomeItem = HomeItem(
    id = book.key,
    title = book.title,
    author = book.author,
    mediaType = when {
        book.pairId != null -> HomeItem.MediaType.PAIR
        book.ebookId != null -> HomeItem.MediaType.EBOOK
        else -> HomeItem.MediaType.AUDIOBOOK
    },
    pairId = book.pairId,
    audiobookId = book.audiobookId,
    ebookId = book.ebookId,
    audiobookCoverPath = book.audiobookCoverPath,
    ebookCoverPath = book.ebookCoverPath,
    series = series,
    seriesIndex = book.index?.toFloat(),
)
