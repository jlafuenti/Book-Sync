package com.booksync.ui.reader

/**
 * The reader's tap-to-cycle progress indicator (issue #730) as plain state:
 * the Android twin of the text logic in the web's `useReaderProgress` hook.
 * [ReaderActivity] feeds it the locator, the live probe and the page count,
 * and shows [text] and [contentDescription]; every rule it applies is one of
 * the shared [ReaderProgress] functions held to the web by the parity
 * fixtures.
 *
 * The page within the chapter comes from [LivePageProbe] (Readium's own CSS
 * columns), and until the probe answers for a new position, from the
 * locator's progression. When the probe and the page count disagree about a
 * section, the probe wins for the chapter state; [onProbe] reports the
 * disagreement once so the caller can log it.
 */
class ReaderProgressState(private val prefs: ReaderProgressPrefs) {

    /** A section whose live page count differs from the counted one. */
    data class Drift(val sectionIndex: Int, val counted: Int, val probed: Int)

    private data class Shown(val sectionIndex: Int, val page: Int, val pages: Int, val atMs: Long)

    /** One of [ReaderProgress.PROGRESS_MODES]; changed only by [cycle], which persists it. */
    var mode: String = prefs.progressMode
        private set

    /** `"ebook"` or `"print"`; setting it normalizes and persists. */
    var pageMode: String = prefs.pageMode
        set(value) {
            field = ReaderProgress.parsePageMode(value)
            prefs.pageMode = field
        }

    /** Pages and characters per reading-order resource, or null until counted. */
    var counts: Counts? = null
        set(value) {
            field = value
            reportedDrift.clear()
        }

    var sectionIndex: Int = -1
        private set
    var pageInChapter: Int = 0
        private set
    var pagesInChapter: Int = 0
        private set

    /** Readium's `totalProgression`, or null when it has not given one. */
    var fraction: Double? = null
        private set
    var printList: ReaderProgress.PrintList? = null
        private set
    var printPageCount: Int? = null
    var samples: List<Double> = prefs.speedSamples
        private set

    /** What `percent` mode shows: the reader's own `"$chapterTitle · $pct%"`. */
    var percentText: String = "…"

    private var pageList = ReaderProgressInputs.PageList(emptyList(), emptyList())
    private var lastShown: Shown? = null
    private val reportedDrift = mutableSetOf<Drift>()

    /** The book's embedded page list, as [ReaderProgressInputs.pageList] builds it. */
    fun setPageList(entries: List<Pair<Int, String>>, labels: List<String>) {
        pageList = ReaderProgressInputs.PageList(entries, labels)
    }

    /** Moves to the next mode, persists it, and returns it. */
    fun cycle(): String {
        mode = ReaderProgress.nextProgressMode(mode)
        prefs.progressMode = mode
        return mode
    }

    /**
     * A new locator. Sets the section and fraction at once, and a provisional
     * page from the locator's in-resource [progression] that [onProbe] then
     * replaces. Entering a new section takes the print label from the earlier
     * sections until the probe says which of this section's markers are passed.
     */
    fun onLocator(sectionIndex: Int, progression: Double?, fraction: Double?) {
        val sameSection = sectionIndex == this.sectionIndex
        this.sectionIndex = sectionIndex
        this.fraction = fraction
        val pages = counts?.counts?.getOrNull(sectionIndex)
            ?: if (sameSection) pagesInChapter else 0
        pagesInChapter = pages
        pageInChapter = ReaderProgress.pageInSection(progression, pages) ?: 0
        if (!sameSection) printList = printListAt(emptySet())
    }

    /**
     * The live probe's answer for [sectionIndex]. Ignored when the reader has
     * already moved to another section. Returns a [Drift] the first time this
     * section's live total disagrees with the count, and null otherwise.
     */
    fun onProbe(sectionIndex: Int, probe: LivePageProbe.Result): Drift? {
        if (sectionIndex != this.sectionIndex || sectionIndex < 0) return null
        pageInChapter = probe.page
        pagesInChapter = probe.total
        printList = printListAt(probe.fragmentsBeforeOrAt)
        val counted = counts?.counts?.getOrNull(sectionIndex) ?: return null
        if (counted == probe.total) return null
        val drift = Drift(sectionIndex, counted, probe.total)
        return if (reportedDrift.add(drift)) drift else null
    }

    /**
     * A page the reader showed at [nowMs]. On a forward, adjacent turn (the
     * next page of the same section, or the first page of the next one) the
     * time spent on the previous page becomes a reading-speed sample - the
     * web's rule. [userTurn] false (a programmatic move: a restore, a slider
     * jump, a re-layout) restarts the clock without sampling. A re-report of
     * the same page keeps the clock running.
     */
    fun onPageShown(sectionIndex: Int, page: Int, pages: Int, nowMs: Long, userTurn: Boolean = true) {
        if (sectionIndex < 0 || page < 1) return
        val prev = lastShown
        if (prev != null && prev.sectionIndex == sectionIndex && prev.page == page) return
        lastShown = Shown(sectionIndex, page, pages, nowMs)
        if (prev == null || !userTurn) return
        val forward = (sectionIndex == prev.sectionIndex && page == prev.page + 1) ||
            (sectionIndex == prev.sectionIndex + 1 && page == 1)
        if (!forward) return
        val chars = counts?.chars?.getOrNull(prev.sectionIndex) ?: return
        val pagesInPrev = prev.pages.takeIf { it > 0 }
            ?: counts?.counts?.getOrNull(prev.sectionIndex)?.takeIf { it > 0 }
            ?: return
        val next = ReaderProgress.addSpeedSample(samples, chars.toDouble() / pagesInPrev, (nowMs - prev.atMs) / 1000.0)
        if (next === samples) return
        samples = next
        prefs.speedSamples = next
    }

    /** True when print pages were asked for but ebook pages are shown. */
    val isEbookFallback: Boolean
        get() = mode == "pages" && pageLabel().kind == "ebook-fallback"

    /** The indicator's text; the ebook-page fallback carries a superscript `ᵉ`. */
    fun text(): String = if (isEbookFallback) "${label()}ᵉ" else label()

    /** The accessible name: the full value, and what the fallback marker means. */
    fun contentDescription(): String =
        "Reading progress: ${label()}${if (isEbookFallback) " (ebook pages)" else ""}, tap to change"

    private fun label(): String = when (mode) {
        "pages" -> ReaderProgress.formatPageLabel(pageLabel())
        "chapter" -> ReaderProgress.formatChapterPage(pageInChapter, pagesInChapter)
        "time" -> {
            val chars = counts?.chars?.getOrNull(sectionIndex)
            if (chars == null) {
                "…"
            } else {
                val cps = ReaderProgress.charsPerSecond(samples)
                ReaderProgress.formatTimeLeft(
                    ReaderProgress.secondsLeftInSection(chars, pageInChapter, pagesInChapter, cps),
                )
            }
        }
        else -> percentText
    }

    private fun pageLabel(): ReaderProgress.PageLabel {
        val ebook = counts?.let { ReaderProgress.ebookPosition(it.counts, sectionIndex, pageInChapter) }
        // Print pages scaled from the count need the book-wide fraction; without
        // it they would read "1 of N". The embedded page list does not need it.
        val scalesFromCount = pageMode == "print" && printList?.lastLabel.isNullOrEmpty() &&
            (printPageCount ?: 0) >= 1
        if (scalesFromCount && fraction == null) return ReaderProgress.PageLabel(null, null, "pending")
        return ReaderProgress.resolvePageLabel(pageMode, fraction, ebook, printList, printPageCount)
    }

    private fun printListAt(before: Set<String>): ReaderProgress.PrintList? =
        if (pageList.entries.isEmpty() || sectionIndex < 0) {
            null
        } else {
            LivePageProbe.printListAt(pageList.entries, pageList.labels, sectionIndex, before)
        }
}

/**
 * The pure inputs [ReaderActivity] derives from the publication for
 * [ReaderProgressState], [LivePageProbe] and [PageCountCache] (issue #730).
 */
object ReaderProgressInputs {

    /**
     * The embedded page list: `(readingOrderIndex, fragment)` per entry in book
     * order, with the parallel raw [labels]. An entry whose path is in no
     * reading-order resource is dropped with its label.
     */
    data class PageList(val entries: List<Pair<Int, String>>, val labels: List<String>) {
        /** The non-empty fragments of [sectionIndex]'s entries, for [LivePageProbe.script]. */
        fun fragmentsIn(sectionIndex: Int): List<String> =
            entries.filter { it.first == sectionIndex && it.second.isNotEmpty() }.map { it.second }
    }

    /**
     * The reading-order index of a locator href: an exact match first, then
     * the reader's file-name rule ([spineIndexForHref]). -1 when neither finds it.
     */
    fun sectionIndexOf(spineHrefs: List<String>, href: String): Int {
        val exact = spineHrefs.indexOf(href)
        return if (exact >= 0) exact else spineIndexForHref(spineHrefs, href)
    }

    /**
     * Resolves `publication.pageList` hrefs against the reading order with the
     * web's rule (`printPages.js` `pageListEntries`): the path before `#` equals
     * a spine href, or either ends in `/` plus the other. The fragment is
     * whatever follows `#`, possibly empty (an empty one never counts as passed,
     * as on the web).
     */
    fun pageList(spineHrefs: List<String>, hrefs: List<String>, labels: List<String>): PageList {
        val entries = mutableListOf<Pair<Int, String>>()
        val kept = mutableListOf<String>()
        hrefs.zip(labels).forEach { (href, label) ->
            val path = href.substringBefore('#')
            val fragment = if ('#' in href) href.substringAfter('#') else ""
            val index = spineHrefs.indexOfFirst { sh ->
                sh.isNotEmpty() && (path == sh || path.endsWith("/$sh") || sh.endsWith("/$path"))
            }
            if (index >= 0) {
                entries += index to fragment
                kept += label
            }
        }
        return PageList(entries, kept)
    }

    /**
     * The spine part of the page-count cache key: the reading order's hrefs and
     * the file's size, so a replaced file with the same spine counts afresh (as
     * the web adds the buffer's byte length).
     */
    fun spineSignature(spineHrefs: List<String>, byteLength: Long): String =
        "${spineHrefs.size}:${spineHrefs.joinToString("|")}:$byteLength"

    /**
     * The live `<html style>` minus the settings that only colour the page -
     * theme colours, the appearance and image filters - so switching between
     * day and night reuses the cached count instead of recounting. Only the
     * cache key uses this; the counter still gets the full style.
     */
    fun layoutStyle(style: String): String =
        style.split(';')
            .map { it.trim() }
            .filter { it.isNotEmpty() && !isColourOnly(it.substringBefore(':').trim()) }
            .joinToString("; ")

    private fun isColourOnly(property: String): Boolean =
        property.endsWith("Color", ignoreCase = true) ||
            property.equals("--USER__appearance", ignoreCase = true) ||
            property.equals("--USER__darkenImages", ignoreCase = true) ||
            property.equals("--USER__invertImages", ignoreCase = true) ||
            property.equals("--USER__darkenFilter", ignoreCase = true) ||
            property.equals("--USER__invertFilter", ignoreCase = true)

    /**
     * The resource's markup up to its `</head>`, with the document closed - all
     * [LiveHeadCapture.script] needs, without shipping a whole chapter body into
     * the live page. Without a `</head>` the markup is passed whole.
     */
    fun rawHead(html: String): String {
        val end = html.indexOf("</head>", ignoreCase = true)
        return if (end < 0) html else html.substring(0, end + "</head>".length) + "</html>"
    }
}
