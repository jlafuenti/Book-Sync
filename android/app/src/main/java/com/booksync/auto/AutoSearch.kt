package com.booksync.auto

/**
 * One book as the voice-search index sees it. Deliberately not a Room entity:
 * pairs and standalone audiobooks are different rows with different column
 * names, and the ranking below should not care which it is looking at.
 */
data class AutoSearchable(
    /** `pair_N` or `audiobook_N` — see `com.booksync.player.MediaId`. */
    val mediaId: String,
    val title: String,
    val author: String? = null,
    val series: String? = null,
)

/** Never hand a car UI (or Assistant) an unbounded result list. */
const val AUTO_MAX_SEARCH_RESULTS = 50

/**
 * Rank a spoken query against the local library (issue #172).
 *
 * `MediaLibrarySession.Callback.onSearch` and the Assistant "play X on Tandem"
 * path both arrive as a fuzzy phrase, so this is the whole feature: turn one
 * string into the book the driver meant, or into nothing.
 *
 * Ranking, best first:
 *   1. the title, exactly
 *   2. a title the query starts
 *   3. a title the query appears inside
 *   4. the author, exactly
 *   5. the series, exactly
 *   6. an author the query appears inside
 *   7. a series the query appears inside
 *
 * Ties keep the caller's order, which is why the service passes Continue
 * Listening first: between two equally good matches the one being listened to
 * is the one meant.
 *
 * A **blank** query is not "no match" — Assistant sends one for "play Tandem",
 * and the car checklist expects that to start something. It returns the library
 * unchanged so the caller can play the first entry.
 *
 * Matching is case- and punctuation-insensitive: "moby dick" has to find
 * "Moby-Dick", because speech recognition never returns the hyphen.
 */
fun autoSearch(query: String, books: List<AutoSearchable>): List<AutoSearchable> {
    val q = normalizeForSearch(query)
    if (q.isEmpty()) return books.take(AUTO_MAX_SEARCH_RESULTS)

    return books
        .mapNotNull { book -> rankOf(q, book)?.let { rank -> book to rank } }
        // sortedBy is stable, so equal ranks keep the caller's order.
        .sortedBy { it.second }
        .map { it.first }
        .take(AUTO_MAX_SEARCH_RESULTS)
}

private fun rankOf(q: String, book: AutoSearchable): Int? {
    val title = normalizeForSearch(book.title)
    val author = book.author?.let { normalizeForSearch(it) }.orEmpty()
    val series = book.series?.let { normalizeForSearch(it) }.orEmpty()
    return when {
        title == q -> 0
        title.startsWith(q) -> 1
        title.contains(q) -> 2
        author.isNotEmpty() && author == q -> 3
        series.isNotEmpty() && series == q -> 4
        author.isNotEmpty() && author.contains(q) -> 5
        series.isNotEmpty() && series.contains(q) -> 6
        else -> null
    }
}

/**
 * Lower case, every non-alphanumeric run collapsed to a single space, trimmed.
 * Punctuation has to go: a spoken title never carries the comma in
 * "Bartleby, the Scrivener" or the hyphen in "Moby-Dick".
 */
private fun normalizeForSearch(raw: String): String {
    val sb = StringBuilder(raw.length)
    for (ch in raw.lowercase()) {
        if (ch.isLetterOrDigit()) sb.append(ch)
        else if (sb.isNotEmpty() && sb.last() != ' ') sb.append(' ')
    }
    return sb.toString().trim()
}
