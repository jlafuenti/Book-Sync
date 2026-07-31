package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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

    // ---------- bookProgressPercent ----------

    private val lengths = longArrayOf(100, 300, 100)  // 500 chars total

    @Test
    fun `bookProgressPercent converts Readium totalProgression from 0-1 to 0-100`() {
        // The web reader sends 0-100 (epub.js percentage * 100); Readium's
        // totalProgression is 0-1. Sending it raw would make the two clients
        // disagree by 100x (issue #61).
        assertEquals(42.0f, bookProgressPercent(0.42, lengths, 0, 0.0)!!, 0.001f)
        assertEquals(0.0f, bookProgressPercent(0.0, lengths, 0, 0.0)!!, 0.001f)
        assertEquals(100.0f, bookProgressPercent(1.0, lengths, 2, 1.0)!!, 0.001f)
    }

    @Test
    fun `bookProgressPercent falls back to spine-weighted position when totalProgression is null`() {
        // Halfway through chapter 2 = (100 + 150) / 500 = 50%.
        assertEquals(50.0f, bookProgressPercent(null, lengths, 1, 0.5)!!, 0.001f)
        // Start of chapter 3 = 400 / 500 = 80%.
        assertEquals(80.0f, bookProgressPercent(null, lengths, 2, 0.0)!!, 0.001f)
    }

    @Test
    fun `bookProgressPercent is clamped to 0-100 and survives degenerate input`() {
        assertEquals(100.0f, bookProgressPercent(1.5, lengths, 2, 1.0)!!, 0.001f)
        assertEquals(0.0f, bookProgressPercent(-0.2, lengths, 0, 0.0)!!, 0.001f)
        // Spine index out of range must not throw.
        assertEquals(0.0f, bookProgressPercent(null, lengths, -1, 0.5)!!, 0.001f)
        assertEquals(100.0f, bookProgressPercent(null, lengths, 9, 0.5)!!, 0.001f)
    }

    @Test
    fun `bookProgressPercent returns null rather than a fake zero when it cannot be computed`() {
        // A locator restored from the text anchor carries no totalProgression,
        // and the chapter lengths may not be computed yet on the first save
        // after opening. Returning 0 there writes "start of book" over a real
        // percent — the same shape of bug as the position loss. Null means
        // "omit", and the server leaves the stored value alone.
        assertNull(bookProgressPercent(null, longArrayOf(), 0, 0.5))
        assertNull(bookProgressPercent(null, longArrayOf(0, 0), 1, 0.5))
    }

    // ---------- isProgrammaticEcho ----------

    @Test
    fun `isProgrammaticEcho is false when nothing has been displayed programmatically yet`() {
        assertEquals(false, isProgrammaticEcho(null, null, "ch01.xhtml", 0.0))
    }

    @Test
    fun `isProgrammaticEcho is false when the href differs from the programmatic target`() {
        assertEquals(false, isProgrammaticEcho("ch01.xhtml", 0.0, "ch02.xhtml", 0.0))
    }

    @Test
    fun `isProgrammaticEcho matches an exact progression on the same href`() {
        assertEquals(true, isProgrammaticEcho("ch01.xhtml", 0.42, "ch01.xhtml", 0.42))
    }

    @Test
    fun `isProgrammaticEcho matches within epsilon of the target progression`() {
        // Readium's settle emission recomputes progression from the rendered
        // WebView layout, so it need not be bit-identical to what was asked for.
        assertEquals(true, isProgrammaticEcho("ch01.xhtml", 0.42000, "ch01.xhtml", 0.42005))
    }

    @Test
    fun `isProgrammaticEcho rejects a progression outside epsilon -- a real page-turn`() {
        assertEquals(false, isProgrammaticEcho("ch01.xhtml", 0.42, "ch01.xhtml", 0.50))
    }

    @Test
    fun `isProgrammaticEcho treats a null progression on either side as matching`() {
        // A locator restored via a coarse rung (chapter-only) carries no
        // progression at all -- there's nothing on that side to disagree with
        // the emitted locator's progression on.
        assertEquals(true, isProgrammaticEcho("ch01.xhtml", null, "ch01.xhtml", 0.37))
        assertEquals(true, isProgrammaticEcho("ch01.xhtml", 0.37, "ch01.xhtml", null))
        assertEquals(true, isProgrammaticEcho("ch01.xhtml", null, "ch01.xhtml", null))
    }

    // ---------- isAtStartOfBook ----------

    @Test
    fun `isAtStartOfBook is true only at spine 0 with negligible progression`() {
        assertEquals(true, isAtStartOfBook(0, null))
        assertEquals(true, isAtStartOfBook(0, 0.0))
        assertEquals(true, isAtStartOfBook(0, 0.005))
        assertEquals(false, isAtStartOfBook(0, 0.02))
        assertEquals(false, isAtStartOfBook(1, 0.0))
        assertEquals(false, isAtStartOfBook(1, null))
    }

    // ---------- buildTextPreview ----------

    @Test
    fun `buildTextPreview extracts a window around the progression point`() {
        val plainText = "0123456789".repeat(30) // 300 chars
        val preview = buildTextPreview(plainText, progression = 0.5, bookTitle = null)
        // charIndex = 150, window is [130, 300) clipped by the +200 cap -> [130, 300)...
        // just assert it's non-empty and drawn from the source text.
        assertTrue(preview.isNotEmpty())
        assertTrue(plainText.contains(preview.take(20)))
    }

    @Test
    fun `buildTextPreview strips a leading chapter heading`() {
        val plainText = "CHAPTER 12. It was a dark and stormy night and nothing else mattered much at all really truly."
        val preview = buildTextPreview(plainText, progression = 0.0, bookTitle = null)
        assertTrue(!preview.lowercase().startsWith("chapter"))
        assertTrue(preview.startsWith("It was a dark"))
    }

    @Test
    fun `buildTextPreview strips a leading book title`() {
        val plainText = "My Great NovelIt was a dark and stormy night and things happened for quite a while after that."
        val preview = buildTextPreview(plainText, progression = 0.0, bookTitle = "My Great Novel")
        assertTrue(preview.startsWith("It was a dark"))
    }
}
