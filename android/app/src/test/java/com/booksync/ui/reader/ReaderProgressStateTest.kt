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

    @Test
    fun `print mode without a count or list falls back to ebook pages with the marker`() {
        val s = state()
        s.pageMode = "print"
        s.counts = Counts(counts = listOf(342, 660), chars = listOf(1, 1))
        s.onLocator(sectionIndex = 1, progression = 0.0, fraction = 0.34)
        s.onProbe(1, LivePageProbe.Result(page = 1, total = 660, fragmentsBeforeOrAt = emptySet()))
        s.cycle()

        assertEquals("343 of 1002ᵉ", s.text())
        assertTrue(s.isEbookFallback)
        assertEquals("Reading progress: 343 of 1002 (ebook pages), tap to change", s.contentDescription())
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
