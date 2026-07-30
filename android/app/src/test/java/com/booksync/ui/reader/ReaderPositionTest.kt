package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the pure position helpers behind issues #40 and #61.
 *
 * These are top-level `internal` functions rather than [ReaderActivity] members
 * so they're testable without an Android runtime or a Readium Publication —
 * the same pattern the repository's sync helpers use.
 */
class ReaderPositionTest {

    private val spine = listOf(
        "EPUB/text/ch01.xhtml",
        "EPUB/text/ch02.xhtml",
        "EPUB/text/ch03.xhtml",
    )

    // ---------- spineIndexForHref ----------

    @Test
    fun `spineIndexForHref matches an exact href`() {
        assertEquals(1, spineIndexForHref(spine, "EPUB/text/ch02.xhtml"))
    }

    @Test
    fun `spineIndexForHref matches on filename when the paths are rooted differently`() {
        // Readium hands back locator hrefs that may be absolute or relative
        // depending on how the publication was opened.
        assertEquals(2, spineIndexForHref(spine, "/ch03.xhtml"))
        assertEquals(0, spineIndexForHref(listOf("/ch01.xhtml"), "EPUB/text/ch01.xhtml"))
    }

    @Test
    fun `spineIndexForHref returns -1 when nothing matches`() {
        assertEquals(-1, spineIndexForHref(spine, "EPUB/text/nope.xhtml"))
        assertEquals(-1, spineIndexForHref(spine, ""))
        assertEquals(-1, spineIndexForHref(emptyList(), "EPUB/text/ch01.xhtml"))
    }

    // ---------- isLocatorStaleForChapter ----------

    @Test
    fun `locator is fresh when its href resolves to the bookmark chapter`() {
        assertFalse(isLocatorStaleForChapter(spine, "EPUB/text/ch02.xhtml", bookmarkChapter = 1))
    }

    @Test
    fun `locator is stale when another device moved the anchor to a different chapter`() {
        // The web reader writes chapter/sentence but no Readium locator, so a
        // leftover locator can point at a chapter the user has long left.
        assertTrue(isLocatorStaleForChapter(spine, "EPUB/text/ch01.xhtml", bookmarkChapter = 2))
    }

    @Test
    fun `locator is stale when its href is not in the spine at all`() {
        assertTrue(isLocatorStaleForChapter(spine, "EPUB/text/gone.xhtml", bookmarkChapter = 1))
    }

    @Test
    fun `locator is trusted when there is no chapter anchor to check it against`() {
        assertFalse(isLocatorStaleForChapter(spine, "EPUB/text/ch01.xhtml", bookmarkChapter = null))
    }

    // ---------- bookProgressPercent ----------

    private val lengths = longArrayOf(100, 300, 100)  // 500 chars total

    @Test
    fun `bookProgressPercent converts Readium totalProgression from 0-1 to 0-100`() {
        // The web reader sends 0-100 (epub.js percentage * 100); Readium's
        // totalProgression is 0-1. Sending it raw would make the two clients
        // disagree by 100x (issue #61).
        assertEquals(42.0f, bookProgressPercent(0.42, lengths, 0, 0.0), 0.001f)
        assertEquals(0.0f, bookProgressPercent(0.0, lengths, 0, 0.0), 0.001f)
        assertEquals(100.0f, bookProgressPercent(1.0, lengths, 2, 1.0), 0.001f)
    }

    @Test
    fun `bookProgressPercent falls back to spine-weighted position when totalProgression is null`() {
        // Halfway through chapter 2 = (100 + 150) / 500 = 50%.
        assertEquals(50.0f, bookProgressPercent(null, lengths, 1, 0.5), 0.001f)
        // Start of chapter 3 = 400 / 500 = 80%.
        assertEquals(80.0f, bookProgressPercent(null, lengths, 2, 0.0), 0.001f)
    }

    @Test
    fun `bookProgressPercent is clamped to 0-100 and survives degenerate input`() {
        assertEquals(100.0f, bookProgressPercent(1.5, lengths, 2, 1.0), 0.001f)
        assertEquals(0.0f, bookProgressPercent(-0.2, lengths, 0, 0.0), 0.001f)
        assertEquals(0.0f, bookProgressPercent(null, longArrayOf(), 0, 0.5), 0.001f)
        // Spine index out of range must not throw.
        assertEquals(0.0f, bookProgressPercent(null, lengths, -1, 0.5), 0.001f)
        assertEquals(100.0f, bookProgressPercent(null, lengths, 9, 0.5), 0.001f)
    }
}
