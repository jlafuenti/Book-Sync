package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #730, task 12. [ReaderProgressState] is the Android twin of the web's
 * `useReaderProgress` text logic; these mirror the web's Task 8 cases 1-4 and
 * 7 (`EbookReader.progress.test.jsx`) with plain values instead of a mocked
 * rendition, plus the Android-only live-probe inputs.
 */
class ReaderProgressStateTest {

    private val twoSections = Counts(counts = listOf(3, 5), chars = listOf(3000, 5000))

    private fun state(backing: FakeSharedPreferences = FakeSharedPreferences()) =
        ReaderProgressState(ReaderProgressPrefs(backing))

    /** Section 1, page 2 of 5, as the live probe reports it. */
    private fun ReaderProgressState.atSection1Page2() {
        onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        onProbe(1, LivePageProbe.Result(page = 2, total = 5, fragmentsBeforeOrAt = emptySet()))
    }

    // ---- Case 1: percent first, pages pending, then counted ----

    @Test
    fun `percent mode shows the reader's own percent text`() {
        val s = state()
        s.percentText = "Chapter 3 · 41%"

        assertEquals("percent", s.mode)
        assertEquals("Chapter 3 · 41%", s.text())
    }

    @Test
    fun `pages mode reads pending until the book is counted, then the book-wide page`() {
        val s = state()
        s.atSection1Page2()

        assertEquals("pages", s.cycle())
        assertEquals("…", s.text())

        s.counts = twoSections
        assertEquals("5 of 8", s.text())
    }

    // ---- Case 2: the full cycle ----

    @Test
    fun `cycling runs pages, chapter, time and back to percent`() {
        val s = state()
        s.percentText = "41%"
        s.counts = twoSections
        s.atSection1Page2()

        assertEquals("pages", s.cycle())
        assertEquals("5 of 8", s.text())
        assertEquals("chapter", s.cycle())
        assertEquals("2 of 5 in chapter", s.text())
        assertEquals("time", s.cycle())
        // 5000 chars at the default 25 cps over pages 2-5 of 5: 160 s.
        assertEquals("3 min left in chapter", s.text())
        assertEquals("percent", s.cycle())
        assertEquals("41%", s.text())
    }

    @Test
    fun `time mode is pending until the section's characters are counted`() {
        val s = state()
        s.atSection1Page2()
        while (s.mode != "time") s.cycle()

        assertEquals("…", s.text())
    }

    @Test
    fun `chapter mode is pending before the page within the chapter is known`() {
        val s = state()
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        while (s.mode != "chapter") s.cycle()

        assertEquals("…", s.text())
    }

    @Test
    fun `a count that lands after an unanswered probe gives the chapter its estimate`() {
        // The open: the locator comes before the count, the live page is not
        // ready for the probe, and no second locator follows until a page turn.
        val s = state()
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        s.onProbed(1, probe = null, shownAtMs = 0, userTurn = false)

        s.counts = twoSections

        assertEquals(2, s.pageInChapter)
        assertEquals(5, s.pagesInChapter)
        assertEquals("pages", s.cycle())
        assertEquals("5 of 8", s.text())
        assertEquals("chapter", s.cycle())
        assertEquals("2 of 5 in chapter", s.text())
        assertEquals("time", s.cycle())
        assertEquals("3 min left in chapter", s.text())
    }

    @Test
    fun `a count that lands after a probe answer leaves the live page alone`() {
        val s = state()
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        s.onProbe(1, LivePageProbe.Result(page = 4, total = 6, fragmentsBeforeOrAt = emptySet()))

        s.counts = twoSections

        assertEquals(4, s.pageInChapter)
        assertEquals(6, s.pagesInChapter)
    }

    @Test
    fun `a probe that disagrees with the locator's page has not settled yet`() {
        // A restore to page 30 of 32: the live page answers once at its top,
        // before Readium has scrolled it to the locator, then where it lands.
        val s = state()
        s.onLocator(sectionIndex = 1, progression = 0.906, fraction = 0.19)

        assertFalse(s.settled(LivePageProbe.Result(page = 1, total = 32, fragmentsBeforeOrAt = emptySet())))
        assertTrue(s.settled(LivePageProbe.Result(page = 30, total = 32, fragmentsBeforeOrAt = emptySet())))
        assertTrue(s.settled(LivePageProbe.Result(page = 29, total = 32, fragmentsBeforeOrAt = emptySet())))
    }

    @Test
    fun `any probe has settled when the locator gave no progression`() {
        val s = state()
        s.onLocator(sectionIndex = 1, progression = null, fraction = null)

        assertTrue(s.settled(LivePageProbe.Result(page = 7, total = 32, fragmentsBeforeOrAt = emptySet())))
    }

    // ---- Case 3: the mode survives a reopen ----

    @Test
    fun `the progress mode is persisted and survives a new state`() {
        val backing = FakeSharedPreferences()
        state(backing).cycle()
        state(backing).cycle()

        assertEquals("chapter", state(backing).mode)
    }

    // ---- Case 4: print pages ----

    @Test
    fun `print mode scales the print page count by the book-wide fraction`() {
        val s = state()
        s.pageMode = "print"
        s.printPageCount = 300
        s.counts = twoSections
        s.atSection1Page2()
        s.cycle()

        assertEquals("120 of 300", s.text())
        assertFalse(s.isEbookFallback)
    }

    /** Print pages asked for, none known: section 1, page 1 of 660, book-wide 343 of 1002. */
    private fun ReaderProgressState.printWithoutPrintPages() {
        pageMode = "print"
        counts = Counts(counts = listOf(342, 660), chars = listOf(1, 1))
        onLocator(sectionIndex = 1, progression = 0.0, fraction = 0.34)
        onProbe(1, LivePageProbe.Result(page = 1, total = 660, fragmentsBeforeOrAt = emptySet()))
    }

    @Test
    fun `cycling into print pages without any shows a notice, then the ebook count`() {
        val s = state()
        s.printWithoutPrintPages()
        s.cycle()

        assertEquals(ReaderProgress.FALLBACK_NOTICE, s.text(nowMs = 10_000))
        assertEquals(1_000L, s.noticeRemainingMs(nowMs = 12_000))
        assertEquals(ReaderProgress.FALLBACK_NOTICE, s.text(nowMs = 12_999))
        assertEquals("343 of 1002", s.text(nowMs = 13_000))
        assertNull(s.noticeRemainingMs(nowMs = 13_000))
        assertTrue(s.isEbookFallback)
        assertEquals("Reading progress: 343 of 1002 (ebook pages), tap to change", s.contentDescription())
    }

    @Test
    fun `the accessible name keeps the count while the notice shows`() {
        val s = state()
        s.printWithoutPrintPages()
        s.cycle()
        s.text(nowMs = 0)

        assertEquals("Reading progress: 343 of 1002 (ebook pages), tap to change", s.contentDescription())
    }

    @Test
    fun `reopening in print pages without any shows the ebook count without a notice`() {
        val backing = FakeSharedPreferences()
        state(backing).apply { pageMode = "print" }.cycle()
        val s = state(backing)
        assertEquals("print", s.pageMode)
        s.printWithoutPrintPages()

        assertEquals("343 of 1002", s.text(nowMs = 0))
        assertNull(s.noticeRemainingMs(nowMs = 0))
    }

    @Test
    fun `the notice waits for a pending count and starts when it resolves`() {
        val s = state()
        s.pageMode = "print"
        s.onLocator(sectionIndex = 1, progression = 0.0, fraction = 0.34)
        s.cycle()
        assertEquals("…", s.text(nowMs = 0))

        s.counts = Counts(counts = listOf(342, 660), chars = listOf(1, 1))

        assertEquals(ReaderProgress.FALLBACK_NOTICE, s.text(nowMs = 20_000))
        assertEquals("343 of 1002", s.text(nowMs = 23_000))
    }

    @Test
    fun `no notice when the book has a print page count`() {
        val s = state()
        s.printWithoutPrintPages()
        s.printPageCount = 300
        s.cycle()

        // ceil(0.34 * 300) in floating point.
        assertEquals("103 of 300", s.text(nowMs = 0))
        // Losing the print pages later does not raise a notice the tap never asked for.
        s.printPageCount = null
        assertEquals("343 of 1002", s.text(nowMs = 1))
    }

    @Test
    fun `choosing print pages while in pages mode shows the notice`() {
        val s = state()
        s.printWithoutPrintPages()
        s.pageMode = "ebook"
        s.cycle()
        assertEquals("343 of 1002", s.text(nowMs = 0))

        s.pageMode = "print"

        assertEquals(ReaderProgress.FALLBACK_NOTICE, s.text(nowMs = 5_000))
    }

    @Test
    fun `cycling away ends the notice and cycling back shows it again`() {
        val s = state()
        s.printWithoutPrintPages()
        s.cycle()
        assertEquals(ReaderProgress.FALLBACK_NOTICE, s.text(nowMs = 0))

        s.cycle()
        assertEquals("1 of 660 in chapter", s.text(nowMs = 1))
        assertNull(s.noticeRemainingMs(nowMs = 1))
        s.cycle()
        s.cycle()
        s.cycle()

        assertEquals(ReaderProgress.FALLBACK_NOTICE, s.text(nowMs = 2))
    }

    @Test
    fun `print mode scaled from a count is pending while the fraction is unknown`() {
        val s = state()
        s.pageMode = "print"
        s.printPageCount = 300
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = null)
        s.cycle()

        assertEquals("…", s.text())
    }

    @Test
    fun `print mode reads the embedded page list at the probed position`() {
        val s = state()
        s.pageMode = "print"
        s.printPageCount = 300
        s.setPageList(
            entries = listOf(0 to "p1", 1 to "p7", 1 to "p8", 1 to "p9"),
            labels = listOf("1", "7", "8", "9"),
        )
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        s.onProbe(1, LivePageProbe.Result(page = 2, total = 5, fragmentsBeforeOrAt = setOf("p7", "p8")))
        s.cycle()

        assertEquals("8 of 9", s.text())
    }

    @Test
    fun `entering a new section takes the page list label from earlier sections until the probe answers`() {
        val s = state()
        s.pageMode = "print"
        s.setPageList(entries = listOf(0 to "p1", 1 to "p7"), labels = listOf("1", "7"))
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.0, fraction = 0.4)
        s.cycle()

        assertEquals("1 of 7", s.text())
    }

    @Test
    fun `the page mode setter persists and normalizes`() {
        val backing = FakeSharedPreferences()
        state(backing).pageMode = "print"
        assertEquals("print", state(backing).pageMode)

        val s = state(backing)
        s.pageMode = "nonsense"
        assertEquals("ebook", s.pageMode)
        assertEquals("ebook", state(backing).pageMode)
    }

    @Test
    fun `the content description carries the shown value`() {
        val s = state()
        s.counts = twoSections
        s.atSection1Page2()
        s.cycle()

        assertEquals("Reading progress: 5 of 8, tap to change", s.contentDescription())
    }

    // ---- The live probe and the provisional page ----

    @Test
    fun `before the probe answers, the page comes from the locator progression`() {
        val s = state()
        s.counts = twoSections
        // Readium's progression for the last of 5 pages is 4/5.
        s.onLocator(sectionIndex = 1, progression = 0.8, fraction = 0.9)

        assertEquals(5, s.pageInChapter)
        assertEquals(5, s.pagesInChapter)
    }

    @Test
    fun `a probe for a section the reader has left is ignored`() {
        val s = state()
        s.counts = twoSections
        s.onLocator(sectionIndex = 0, progression = 0.0, fraction = 0.0)
        val drift = s.onProbe(1, LivePageProbe.Result(page = 4, total = 9, fragmentsBeforeOrAt = emptySet()))

        assertNull(drift)
        assertEquals(1, s.pageInChapter)
        assertEquals(3, s.pagesInChapter)
    }

    @Test
    fun `the probe wins over the count for the chapter and drift is reported once`() {
        val s = state()
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)

        val first = s.onProbe(1, LivePageProbe.Result(page = 2, total = 6, fragmentsBeforeOrAt = emptySet()))
        val again = s.onProbe(1, LivePageProbe.Result(page = 3, total = 6, fragmentsBeforeOrAt = emptySet()))

        assertEquals(ReaderProgressState.Drift(sectionIndex = 1, counted = 5, probed = 6), first)
        assertNull(again)
        while (s.mode != "chapter") s.cycle()
        assertEquals("3 of 6 in chapter", s.text())
    }

    @Test
    fun `a late probe for a section the reader has left records no page and no sample`() {
        val s = state()
        s.counts = Counts(counts = listOf(3, 5, 4), chars = listOf(3000, 5000, 4000))
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        assertTrue(s.onProbed(1, LivePageProbe.Result(2, 5, emptySet()), shownAtMs = 0, userTurn = true).accepted)

        // The reader turns into section 2; section 1's probe for a later page answers late.
        s.onLocator(sectionIndex = 2, progression = 0.0, fraction = 0.5)
        val stale = s.onProbed(1, LivePageProbe.Result(3, 5, emptySet()), shownAtMs = 60_000, userTurn = true)

        assertFalse(stale.accepted)
        assertNull(stale.drift)
        assertEquals(1, s.pageInChapter)
        assertEquals(4, s.pagesInChapter)
        assertTrue(s.samples.isEmpty())

        // The next real turn still measures from section 1 page 2 (shown at 0), not from the stale answer.
        s.onProbed(2, LivePageProbe.Result(1, 4, emptySet()), shownAtMs = 120_000, userTurn = true)
        assertEquals(listOf(1000.0 / 120), s.samples)
    }

    @Test
    fun `an accepted probe without an answer records the locator's page`() {
        val s = state()
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)
        assertTrue(s.onProbed(1, null, shownAtMs = 0, userTurn = true).accepted)
        s.onLocator(sectionIndex = 1, progression = 0.4, fraction = 0.5)
        s.onProbed(1, null, shownAtMs = 60_000, userTurn = true)

        assertEquals(listOf(1000.0 / 60), s.samples)
    }

    @Test
    fun `an accepted probe passes on the drift`() {
        val s = state()
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)

        val probed = s.onProbed(1, LivePageProbe.Result(2, 6, emptySet()), shownAtMs = 0, userTurn = true)

        assertTrue(probed.accepted)
        assertEquals(ReaderProgressState.Drift(1, counted = 5, probed = 6), probed.drift)
    }

    @Test
    fun `a probe that agrees with the count reports no drift`() {
        val s = state()
        s.counts = twoSections
        s.onLocator(sectionIndex = 1, progression = 0.2, fraction = 0.4)

        assertNull(s.onProbe(1, LivePageProbe.Result(page = 2, total = 5, fragmentsBeforeOrAt = emptySet())))
    }

    // ---- Case 7: dwell samples ----

    @Test
    fun `a forward adjacent turn after 60 s adds one speed sample`() {
        val backing = FakeSharedPreferences()
        val s = state(backing)
        s.counts = twoSections
        s.onPageShown(sectionIndex = 1, page = 2, pages = 5, nowMs = 0)
        s.onPageShown(sectionIndex = 1, page = 3, pages = 5, nowMs = 60_000)

        // 5000 chars / 5 pages = 1000 chars on the page, read in 60 s.
        assertEquals(listOf(1000.0 / 60), s.samples)
        assertEquals(listOf(1000.0 / 60), ReaderProgressPrefs(backing).speedSamples)
    }

    @Test
    fun `turning into the next section on its first page counts as forward`() {
        val s = state()
        s.counts = twoSections
        s.onPageShown(sectionIndex = 0, page = 3, pages = 3, nowMs = 0)
        s.onPageShown(sectionIndex = 1, page = 1, pages = 5, nowMs = 30_000)

        assertEquals(listOf(1000.0 / 30), s.samples)
    }

    @Test
    fun `a backward turn, a jump across sections and a quick turn add no sample`() {
        val s = state()
        s.counts = Counts(counts = listOf(3, 5, 4), chars = listOf(3000, 5000, 4000))
        s.onPageShown(sectionIndex = 1, page = 3, pages = 5, nowMs = 0)
        s.onPageShown(sectionIndex = 1, page = 2, pages = 5, nowMs = 60_000) // backward
        s.onPageShown(sectionIndex = 1, page = 4, pages = 5, nowMs = 120_000) // skips a page
        s.onPageShown(sectionIndex = 2, page = 2, pages = 4, nowMs = 180_000) // next section, not page 1
        s.onPageShown(sectionIndex = 2, page = 3, pages = 4, nowMs = 181_000) // 1 s dwell

        assertTrue(s.samples.isEmpty())
    }

    @Test
    fun `a re-report of the same page keeps the dwell clock running`() {
        val s = state()
        s.counts = twoSections
        s.onPageShown(sectionIndex = 1, page = 2, pages = 5, nowMs = 0)
        s.onPageShown(sectionIndex = 1, page = 2, pages = 5, nowMs = 30_000)
        s.onPageShown(sectionIndex = 1, page = 3, pages = 5, nowMs = 60_000)

        assertEquals(listOf(1000.0 / 60), s.samples)
    }

    @Test
    fun `a programmatic move resets the dwell clock without sampling`() {
        val s = state()
        s.counts = twoSections
        s.onPageShown(sectionIndex = 1, page = 1, pages = 5, nowMs = 0)
        // An echo landing on the adjacent page (a slider drag, a re-layout).
        s.onPageShown(sectionIndex = 1, page = 2, pages = 5, nowMs = 60_000, userTurn = false)
        assertTrue(s.samples.isEmpty())

        s.onPageShown(sectionIndex = 1, page = 3, pages = 5, nowMs = 90_000)
        assertEquals(listOf(1000.0 / 30), s.samples)
    }

    @Test
    fun `no sample is taken before the book is counted`() {
        val s = state()
        s.onPageShown(sectionIndex = 1, page = 2, pages = 5, nowMs = 0)
        s.onPageShown(sectionIndex = 1, page = 3, pages = 5, nowMs = 60_000)

        assertTrue(s.samples.isEmpty())
    }

    @Test
    fun `stored samples feed the time estimate`() {
        val backing = FakeSharedPreferences()
        ReaderProgressPrefs(backing).speedSamples = List(5) { 50.0 }
        val s = state(backing)
        s.counts = twoSections
        s.atSection1Page2()
        while (s.mode != "time") s.cycle()

        // 4000 chars left at 50 cps: 80 s.
        assertEquals("2 min left in chapter", s.text())
    }
}
