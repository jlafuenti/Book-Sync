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

/** Epsilon used by [isProgrammaticEcho] to compare progression values. */
internal const val PROGRAMMATIC_ECHO_EPSILON = 0.001

/**
 * Whether a navigator locator emission is Readium's programmatic echo of the
 * last position the code displayed on purpose (the initial restored locator,
 * or a `navigator.go(...)` call such as `goToProgress`) rather than a real
 * user page-turn (issue #61/#40 fix 1).
 *
 * Readium's `currentLocator` StateFlow emits a SECOND time right after any
 * programmatic display settles in the WebView — same href, a computed
 * `progression` that may differ trivially from what was requested (or be
 * entirely absent on either side) — with no user input involved at all. The
 * old code counted only the very first emission as "not navigation"
 * (`awaitingRestoreLocator`, a single-shot flag never reset on tracker
 * re-entry); that settle emission slipped through as the "first real"
 * emission and got misread as [PositionSavePolicy.onUserNavigation], which
 * could upgrade an [PositionSavePolicy.RestoreOutcome.Unresolved] restore to
 * [PositionSavePolicy.SaveVerdict.FullSave] and write spine 0 over a real
 * server position on the next save.
 *
 * [targetHref] is the href of the last programmatic target; null means
 * nothing has been displayed programmatically yet (that case is NOT an
 * echo — there is nothing to compare against). Progression is treated as
 * matching whenever either side is null: a locator carrying no progression
 * information has nothing to disagree with the target on.
 */
internal fun isProgrammaticEcho(
    targetHref: String?,
    targetProgression: Double?,
    emittedHref: String,
    emittedProgression: Double?,
    epsilon: Double = PROGRAMMATIC_ECHO_EPSILON,
): Boolean {
    if (targetHref == null || targetHref != emittedHref) return false
    if (targetProgression == null || emittedProgression == null) return true
    return kotlin.math.abs(emittedProgression - targetProgression) < epsilon
}

/**
 * Whether a position is still "start of book" — spine 0 and effectively no
 * progression into it. Backs [PositionSavePolicy.verdictForSave]'s
 * `atStartOfBook` safety net (issue #61/#40 fix 1b): an unresolved restore
 * displays the start, and no per-emission signal reliably tells a real
 * page-turn apart from Readium's settle emission — but whether the reader
 * has actually moved off the start is trustworthy either way.
 */
internal fun isAtStartOfBook(spineIndex: Int, progression: Double?): Boolean =
    spineIndex == 0 && (progression ?: 0.0) < 0.01

/**
 * Extract a display-ready text preview window from a chapter's plain text
 * around [progression], stripping chapter headings and a leading book title.
 *
 * Pulled out as a pure function so `ReaderActivity`'s cache-only,
 * synchronous preview lookup (issue #61/#40 fix 2 — the reader-position
 * capture must not touch Jsoup/IO on the save path) is unit-testable without
 * an Android runtime or a Readium Publication, same as everything else here.
 */
internal fun buildTextPreview(plainText: String, progression: Double, bookTitle: String?): String {
    val charIndex = (plainText.length * progression).toInt()
    val startIndex = maxOf(0, charIndex - 20)
    val endIndex = minOf(charIndex + 200, plainText.length)
    // Extract a focused window, strip chapter headings.
    // Handle "CHAPTER N, Title CHAPTER N" pattern (Jsoup has no newlines).
    var text = plainText.substring(startIndex, endIndex)
        .replace(Regex("(?i)^chapter\\s+\\d+.{0,120}?chapter\\s+\\d+\\s*"), "")
        .replace(Regex("(?i)^chapter\\s+\\d+[,.]?\\s*"), "")
        .replace(Regex("(?i)^prologue[,.]?\\s*"), "")
        .trim()
    // Strip book title from start (Jsoup includes <title> text at top of chapter).
    if (!bookTitle.isNullOrEmpty()) {
        val titlePattern = Regex("^${Regex.escape(bookTitle)}\\s*", RegexOption.IGNORE_CASE)
        text = titlePattern.replace(text, "") // Remove first occurrence
        text = titlePattern.replace(text, "") // Remove possible second occurrence
        text = text.trim()
    }
    return text
}
