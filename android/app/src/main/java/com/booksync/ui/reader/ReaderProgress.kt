package com.booksync.ui.reader

import kotlin.math.ceil
import kotlin.math.roundToInt

/** Issue #730. One rule with web/src/lib/readerProgress.js; held to reader_progress_cases.json. */
object ReaderProgress {
    val PROGRESS_MODES = listOf("percent", "pages", "chapter", "time")
    const val DEFAULT_CHARS_PER_SECOND = 25.0
    private const val MIN_DWELL_S = 2.0
    private const val MAX_DWELL_S = 300.0
    private const val MIN_SAMPLES = 5
    private const val MAX_SAMPLES = 50

    fun nextProgressMode(mode: String?): String =
        PROGRESS_MODES[(PROGRESS_MODES.indexOf(mode) + 1) % PROGRESS_MODES.size]
    fun parseProgressMode(v: String?): String = if (v in PROGRESS_MODES) v!! else "percent"
    fun parsePageMode(v: String?): String = if (v == "print") "print" else "ebook"

    fun pageInSection(progression: Double?, total: Int): Int? {
        if (total < 1) return null
        return (((progression ?: 0.0) * total).roundToInt() + 1).coerceIn(1, total)
    }

    data class EbookPosition(val page: Int, val total: Int)
    fun ebookPosition(counts: List<Int>, sectionIndex: Int, page: Int): EbookPosition? {
        if (sectionIndex < 0 || sectionIndex >= counts.size) return null
        val before = counts.take(sectionIndex).sum()
        val inSection = page.coerceAtLeast(1).coerceAtMost(counts[sectionIndex].coerceAtLeast(1))
        return EbookPosition(before + inSection, counts.sum())
    }

    data class PrintList(val currentLabel: String?, val firstLabel: String?, val lastLabel: String?)
    data class PageLabel(val current: String?, val total: String?, val kind: String)
    fun resolvePageLabel(pageMode: String, fraction: Double?, ebook: EbookPosition?, printList: PrintList?, printPageCount: Int?): PageLabel {
        if (pageMode == "print") {
            if (!printList?.lastLabel.isNullOrEmpty()) return PageLabel(printList.currentLabel ?: printList.firstLabel, printList.lastLabel, "print")
            if (printPageCount != null && printPageCount >= 1) {
                val f = (fraction ?: 0.0).coerceIn(0.0, 1.0)
                val current = ceil(f * printPageCount).toInt().coerceIn(1, printPageCount)
                return PageLabel(current.toString(), printPageCount.toString(), "print")
            }
        }
        if (ebook != null) return PageLabel(ebook.page.toString(), ebook.total.toString(), if (pageMode == "print") "ebook-fallback" else "ebook")
        return PageLabel(null, null, "pending")
    }
    fun formatPageLabel(r: PageLabel): String = if (r.kind == "pending") "…" else "${r.current} of ${r.total}"
    fun formatChapterPage(page: Int?, total: Int?): String =
        if (page != null && page > 0 && total != null && total > 0) "$page of $total in chapter" else "…"

    fun addSpeedSample(samples: List<Double>, charsOnPage: Double, dwellSeconds: Double): List<Double> {
        if (charsOnPage <= 0 || dwellSeconds < MIN_DWELL_S || dwellSeconds > MAX_DWELL_S) return samples
        return (samples + charsOnPage / dwellSeconds).takeLast(MAX_SAMPLES)
    }
    fun charsPerSecond(samples: List<Double>): Double {
        if (samples.size < MIN_SAMPLES) return DEFAULT_CHARS_PER_SECOND
        val s = samples.sorted(); val m = s.size / 2
        return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2
    }
    fun secondsLeftInSection(sectionChars: Int, page: Int, pages: Int, cps: Double): Double? {
        if (sectionChars < 0 || pages < 1 || cps <= 0) return null
        val p = page.coerceIn(1, pages)
        return sectionChars * (1 - (p - 1).toDouble() / pages) / cps
    }
    fun formatTimeLeft(seconds: Double?): String {
        if (seconds == null) return "…"
        if (seconds < 60) return "<1 min left in chapter"
        val mins = ceil(seconds / 60).toInt()
        return if (mins < 60) "$mins min left in chapter" else "${mins / 60} h ${mins % 60} min left in chapter"
    }
}
