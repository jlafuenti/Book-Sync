package com.booksync.ui.reader

import com.booksync.data.local.entity.SyncPointEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Quote context for the read-along highlight (issue #793). */
class SentenceQuoteIndexTest {

    private fun point(chapter: Int, sentence: Int, preview: String?) =
        SyncPointEntity(
            bookPairId = 1, epubChapter = chapter, epubSentenceIndex = sentence,
            epubTextPreview = preview, audioStartMs = sentence * 1000, audioEndMs = sentence * 1000 + 900,
        )

    private fun indexOf(vararg points: SyncPointEntity) = SentenceQuoteIndex(points.toList())

    @Test
    fun `a middle sentence gets the tail of the previous and the head of the next`() {
        val a = point(0, 0, "The first sentence of the test chapter ends right here.")
        val b = point(0, 1, "The middle one.")
        val c = point(0, 2, "A closing sentence that carries on for a good while longer.")
        val q = indexOf(a, b, c).quoteFor(b)!!
        assertEquals("The middle one.", q.highlight)
        assertEquals("test chapter ends right here.", q.before!!.takeLast(29))
        assertEquals(SentenceQuoteIndex.CONTEXT_CHARS, q.before.length)
        assertEquals("A closing sentence that carries on for", q.after!!.take(38))
        assertEquals(SentenceQuoteIndex.CONTEXT_CHARS, q.after.length)
    }

    @Test
    fun `short neighbours are used whole`() {
        val mid = point(0, 1, "No.")
        val q = indexOf(point(0, 0, "Yes."), mid, point(0, 2, "Maybe.")).quoteFor(mid)!!
        assertEquals("Yes.", q.before)
        assertEquals("Maybe.", q.after)
    }

    @Test
    fun `two occurrences of a repeated line resolve to different context`() {
        val first = point(2, 2372, "Narrator : stand by.")
        val second = point(2, 2382, "Narrator : stand by.")
        val index = indexOf(
            point(2, 2371, "The hatch was sealed from the outside."),
            first,
            point(2, 2373, "Nothing moved in the corridor."),
            point(2, 2381, "Hours later the same order came again."),
            second,
            point(2, 2383, "Then the lights went out."),
        )
        val q1 = index.quoteFor(first)!!
        val q2 = index.quoteFor(second)!!
        assertEquals(q1.highlight, q2.highlight)
        assertNotEquals(q1.before, q2.before)
        assertNotEquals(q1.after, q2.after)
    }

    @Test
    fun `chapter edges have no context on the missing side`() {
        val first = point(3, 0, "Opening line.")
        val second = point(3, 1, "Middle line.")
        val last = point(3, 2, "Closing line.")
        val otherChapter = point(4, 0, "A different chapter starts here.")
        val index = indexOf(first, second, last, otherChapter)
        assertNull(index.quoteFor(first)!!.before)
        assertEquals("Middle line.", index.quoteFor(first)!!.after)
        assertEquals("Middle line.", index.quoteFor(last)!!.before)
        assertNull("the next chapter is not a neighbour", index.quoteFor(last)!!.after)
        assertNull(index.quoteFor(otherChapter)!!.before)
    }

    @Test
    fun `a neighbour with no preview contributes nothing`() {
        val mid = point(0, 1, "Alone.")
        val q = indexOf(point(0, 0, null), mid, point(0, 2, "  \n ")).quoteFor(mid)!!
        assertNull(q.before)
        assertNull(q.after)
    }

    @Test
    fun `a gap in the sentence indexes means no neighbour`() {
        val mid = point(0, 5, "Isolated.")
        val q = indexOf(point(0, 3, "Far before."), mid, point(0, 8, "Far after.")).quoteFor(mid)!!
        assertNull(q.before)
        assertNull(q.after)
    }

    @Test
    fun `a truncated previous preview is not used as before`() {
        val long = "x".repeat(SentenceQuoteIndex.PREVIEW_CAP)
        val mid = point(0, 1, "Short.")
        val q = indexOf(point(0, 0, long), mid, point(0, 2, long)).quoteFor(mid)!!
        assertNull("a capped preview's tail is not the real tail", q.before)
        assertEquals("the head of a capped preview is real", SentenceQuoteIndex.CONTEXT_CHARS, q.after!!.length)
    }

    @Test
    fun `a preview just under the cap still counts as whole`() {
        val almost = "y".repeat(SentenceQuoteIndex.PREVIEW_CAP - 1)
        val mid = point(0, 1, "Short.")
        val q = indexOf(point(0, 0, almost), mid).quoteFor(mid)!!
        assertEquals(SentenceQuoteIndex.CONTEXT_CHARS, q.before!!.length)
    }

    @Test
    fun `a truncated current preview has no after because the sentence goes on`() {
        val long = "z".repeat(SentenceQuoteIndex.PREVIEW_CAP)
        val cur = point(0, 1, long)
        val q = indexOf(point(0, 0, "Before it."), cur, point(0, 2, "After it.")).quoteFor(cur)!!
        assertEquals(long, q.highlight)
        assertEquals("Before it.", q.before)
        assertNull(q.after)
    }

    @Test
    fun `multi-line neighbours contribute the line next to the quote`() {
        val prev = point(0, 0, "A paragraph ended.\nThe next one closes the previous sentence here.")
        val cur = point(0, 1, "First line of the quote.")
        val next = point(0, 2, "Head of the next sentence.\nIts second paragraph.")
        val q = indexOf(prev, cur, next).quoteFor(cur)!!
        assertEquals("The next one closes the previous sentence here.".takeLast(SentenceQuoteIndex.CONTEXT_CHARS), q.before)
        assertEquals("Head of the next sentence.", q.after)
        assertEquals("First line of the quote.", q.highlight)
    }

    @Test
    fun `a multi-line current preview takes after from its own second line`() {
        val cur = point(0, 1, "“Her brother …”\nHe nodded and left the room.")
        val q = indexOf(point(0, 0, "Before."), cur, point(0, 2, "Unrelated next.")).quoteFor(cur)!!
        assertEquals("“Her brother …”", q.highlight)
        assertEquals("He nodded and left the room.", q.after)
    }

    @Test
    fun `no usable preview means no quote`() {
        assertNull(indexOf(point(0, 0, null)).quoteFor(point(0, 0, null)))
        assertNull(indexOf(point(0, 0, " \n ")).quoteFor(point(0, 0, " \n ")))
    }

    @Test
    fun `the visibility script embeds all three strings escaped`() {
        val js = sentenceVisibilityScript("say \"hi\"", "line\\one", "two\nlines")
        assertTrue(js.contains("\"say \\\"hi\\\"\""))
        assertTrue(js.contains("\"line\\\\one\""))
        assertTrue(js.contains("\"two\\nlines\""))
        val bare = sentenceVisibilityScript("q", null, null)
        assertTrue(bare.contains("(\"q\", \"\", \"\")"))
    }
}
