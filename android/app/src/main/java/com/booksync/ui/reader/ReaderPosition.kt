package com.booksync.ui.reader

/**
 * Pure position helpers for the reader (issues #40, #61).
 *
 * Top-level `internal` functions rather than [ReaderActivity] members so they
 * can be unit-tested without an Android runtime or a Readium Publication.
 */

/**
 * Index of the spine item [locatorHref] refers to, or -1 if none matches.
 *
 * Readium locator hrefs and publication reading-order hrefs are not always
 * rooted the same way (absolute vs. relative to the EPUB root), so an exact
 * comparison is tried first and a filename comparison second.
 */
internal fun spineIndexForHref(readingOrderHrefs: List<String>, locatorHref: String): Int {
    if (locatorHref.isEmpty()) return -1
    val locFile = locatorHref.substringAfterLast("/")
    return readingOrderHrefs.indexOfFirst { pubHref ->
        pubHref == locatorHref ||
            (locFile.isNotEmpty() && pubHref.endsWith(locFile)) ||
            locatorHref.endsWith(pubHref.substringAfterLast("/"))
    }
}

/**
 * Book-level reading progress as a percentage (0-100) for `UserProgress`.
 *
 * The web reader sends 0-100 (epub.js `percentage * 100`) while Readium's
 * `totalProgression` is 0-1, so the two clients would disagree by 100x if the
 * raw value were sent (issue #61).
 *
 * [totalProgression] is absent for some publications; the fallback weights the
 * spine by chapter length, the inverse of what `goToProgress` does to turn a
 * percentage back into a locator.
 *
 * Returns null when neither source is usable, so the caller omits the field
 * rather than reporting 0%.
 */
internal fun bookProgressPercent(
    totalProgression: Double?,
    chapterLengths: LongArray,
    spineIndex: Int,
    chapterProgression: Double,
): Float? {
    if (totalProgression != null) {
        return (totalProgression * 100).coerceIn(0.0, 100.0).toFloat()
    }
    val total = chapterLengths.sum()
    // Nothing to compute from. Returning 0 here would write "start of book"
    // over a real percent — a locator restored from the text anchor carries no
    // totalProgression, and the chapter lengths aren't always ready on the
    // first save after opening. Null means "omit"; the server leaves the
    // stored value alone.
    if (total <= 0L) return null
    if (spineIndex < 0) return 0f
    if (spineIndex >= chapterLengths.size) return 100f
    val before = chapterLengths.take(spineIndex).sum()
    val within = chapterLengths[spineIndex] * chapterProgression.coerceIn(0.0, 1.0)
    return ((before + within) / total * 100).coerceIn(0.0, 100.0).toFloat()
}
