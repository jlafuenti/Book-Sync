package com.booksync.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Fuzzy-pass internals (issue #82), mirroring the server's counterparts in
 * `server/tests/test_sync_matching.py`. These are edge cases the shared golden
 * vectors in `match_cases.json` can't reach through the public `match` entry
 * point — a short needle, an over-long needle, a sub-threshold score, and the
 * ±3 window of the interpolation nudge. Where the two implementations are
 * numerically identical, the expected values here are the Python ones verbatim.
 */
class SyncMatcherInternalsTest {

    private data class Point(
        override val epubChapter: Int,
        override val epubSentenceIndex: Int,
        override val epubTextPreview: String?,
        override val confidence: Float,
    ) : MatchablePoint

    // ---- bigramSet ---------------------------------------------------------

    @Test
    fun `bigramSet yields one entry per adjacent character pair`() {
        // "abcd" -> ab, bc, cd
        assertEquals(3, SyncMatcher.bigramSet("abcd").size)
    }

    @Test
    fun `bigramSet collapses repeated pairs`() {
        // "aaaa" -> only the single pair "aa"
        assertEquals(1, SyncMatcher.bigramSet("aaaa").size)
    }

    @Test
    fun `bigramSet of a string shorter than two characters is empty`() {
        assertTrue(SyncMatcher.bigramSet("").isEmpty())
        assertTrue(SyncMatcher.bigramSet("a").isEmpty())
    }

    // ---- diceSimilarity ----------------------------------------------------

    @Test
    fun `diceSimilarity is 1 for identical sets and 0 against an empty set`() {
        val a = SyncMatcher.bigramSet("the quick brown fox")
        assertEquals(1.0, SyncMatcher.diceSimilarity(a, a), 0.0)
        assertEquals(0.0, SyncMatcher.diceSimilarity(a, SyncMatcher.bigramSet("")), 0.0)
    }

    @Test
    fun `diceSimilarity is 0 for disjoint sets`() {
        val a = SyncMatcher.bigramSet("abab")   // {ab, ba}
        val b = SyncMatcher.bigramSet("cdcd")   // {cd, dc}
        assertEquals(0.0, SyncMatcher.diceSimilarity(a, b), 0.0)
    }

    @Test
    fun `diceSimilarity lands between 0 and 1 for a near miss`() {
        val a = SyncMatcher.bigramSet("the old clock struck midnight")
        val b = SyncMatcher.bigramSet("the old clock struck midnite")
        val score = SyncMatcher.diceSimilarity(a, b)
        assertTrue("expected a high but imperfect score, got $score", score in 0.7..0.999)
    }

    // ---- fuzzyFindInTranscript --------------------------------------------

    @Test
    fun `fuzzyFind rejects a needle shorter than 20 characters`() {
        val transcript = "the old clock in the hallway struck midnight and the house fell silent "
        assertNull(SyncMatcher.fuzzyFindInTranscript(transcript, "too short"))
    }

    @Test
    fun `fuzzyFind rejects a transcript shorter than the needle`() {
        val needle = "the old clock in the hallway struck midnight and the house fell silent"
        assertNull(SyncMatcher.fuzzyFindInTranscript("the old clock in the hall", needle))
    }

    @Test
    fun `fuzzyFind refines to the exact offset`() {
        val needle = "the old clock in the hallway struck midnight and the house fell silent"
        // Pad by 7 chars — not a multiple of the coarse step, so only the step-1
        // refinement pass can land on the true offset with a perfect score.
        val transcript = "abcdefg" + needle + " and then nobody stirred upstairs for a very long while "

        val hit = SyncMatcher.fuzzyFindInTranscript(transcript, needle)

        assertNotNull(hit)
        assertEquals(7, hit!!.first)
        assertEquals(1.0, hit.second, 0.0)
    }

    @Test
    fun `fuzzyFind returns null below the threshold`() {
        val needle = "the old clock in the hallway struck midnight and the house fell silent"
        val transcript = "bananas and helicopters collided noisily above the purple accounting firm downtown "
        assertNull(SyncMatcher.fuzzyFindInTranscript(transcript, needle))
    }

    // ---- nudgeToConfidentPoint --------------------------------------------

    @Test
    fun `nudge keeps an already-confident match`() {
        val points = listOf(Point(2, 0, "a", 0.9f), Point(2, 1, "b", 0.8f))
        assertSame(points[1], SyncMatcher.nudgeToConfidentPoint(points, 1))
    }

    @Test
    fun `nudge moves an interpolated match to a confident neighbour`() {
        val points = listOf(
            Point(2, 0, "a", 0.0f),
            Point(2, 1, "b", 0.9f),
        )
        assertSame(points[1], SyncMatcher.nudgeToConfidentPoint(points, 0))
    }

    @Test
    fun `nudge ignores confident points beyond three positions`() {
        val points = (0..5).map { Point(2, it, "x", 0.0f) } + Point(2, 6, "x", 0.9f)
        // index 0 is interpolated; the only confident point is 6 positions away.
        assertSame(points[0], SyncMatcher.nudgeToConfidentPoint(points, 0))
    }

    @Test
    fun `nudge ignores neighbours that are only weakly confident`() {
        // 0.5 is not > 0.5 — the filter is strict, so this neighbour doesn't qualify.
        val points = listOf(Point(2, 0, "a", 0.0f), Point(2, 1, "b", 0.5f))
        assertSame(points[0], SyncMatcher.nudgeToConfidentPoint(points, 0))
    }

    @Test
    fun `nudge picks the confident neighbour nearest by sentence index`() {
        val points = listOf(
            Point(2, 10, "far behind", 0.9f),
            Point(2, 40, "just ahead", 0.9f),
            Point(2, 41, "the interpolated hit", 0.0f),
        )
        assertSame(points[1], SyncMatcher.nudgeToConfidentPoint(points, 2))
    }
}
