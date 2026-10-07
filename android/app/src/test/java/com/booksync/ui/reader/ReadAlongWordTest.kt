package com.booksync.ui.reader

import com.booksync.data.local.entity.SyncPointEntity
import com.booksync.ui.reader.ReadAlongController.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The word-level half of [ReadAlongController] (issue #836). */
class ReadAlongWordTest {

    private fun point(chapter: Int, sentence: Int, startMs: Int, endMs: Int) =
        SyncPointEntity(
            bookPairId = 1, epubChapter = chapter, epubSentenceIndex = sentence,
            epubTextPreview = "One two three of sentence $chapter.$sentence.",
            audioStartMs = startMs, audioEndMs = endMs, confidence = 1f,
        )

    private val p0 = point(2, 0, 10_000, 13_000)
    private val p1 = point(2, 1, 13_000, 16_000)
    private val p2 = point(3, 0, 16_000, 20_000)
    private val points = listOf(p0, p1, p2)

    private val words = mapOf(
        (2 to 0) to intArrayOf(10_000, 11_000, 12_000),
        (3 to 0) to intArrayOf(16_500, 17_000),
    )

    private fun following(map: Map<Pair<Int, Int>, IntArray> = words) =
        ReadAlongController(points, map).apply { start(0L) }

    @Test
    fun `the first poll in a sentence decorates it and then marks the word`() {
        val c = following()
        assertEquals(listOf(Action.Decorate(p0), Action.Word(p0, 0)), c.onAudioPosition(10_500, 100L))
    }

    @Test
    fun `the same word again yields nothing`() {
        val c = following()
        c.onAudioPosition(10_500, 100L)
        assertTrue(c.onAudioPosition(10_900, 250L).isEmpty())
    }

    @Test
    fun `a word boundary is inclusive of the start time`() {
        val c = following()
        c.onAudioPosition(10_999, 100L)
        assertEquals(listOf(Action.Word(p0, 1)), c.onAudioPosition(11_000, 250L))
    }

    @Test
    fun `the token changing inside one sentence yields only a Word`() {
        val c = following()
        c.onAudioPosition(10_000, 100L)
        val actions = c.onAudioPosition(12_400, 250L)
        assertEquals(listOf(Action.Word(p0, 2)), actions)
    }

    @Test
    fun `a sentence change yields Decorate then Word`() {
        val c = following()
        c.onAudioPosition(12_400, 100L)
        val actions = c.onAudioPosition(17_200, 600L)
        assertEquals(listOf(Action.Decorate(p2), Action.Word(p2, 1)), actions)
    }

    @Test
    fun `before the first word start the token is minus one`() {
        val c = following()
        assertEquals(listOf(Action.Decorate(p2), Action.Word(p2, -1)), c.onAudioPosition(16_100, 100L))
        assertEquals(listOf(Action.Word(p2, 0)), c.onAudioPosition(16_500, 250L))
    }

    @Test
    fun `a sentence without word timing says so once and then stays quiet`() {
        val c = following()
        // p1 is not in the words map (an interpolated point).
        assertEquals(listOf(Action.Decorate(p1), Action.Word(p1, -1)), c.onAudioPosition(14_000, 100L))
        assertTrue(c.onAudioPosition(15_000, 250L).isEmpty())
    }

    @Test
    fun `without any words every sentence still decorates and clears the mark`() {
        val c = following(emptyMap())
        assertEquals(listOf(Action.Decorate(p0), Action.Word(p0, -1)), c.onAudioPosition(10_500, 100L))
        assertTrue(c.onAudioPosition(12_000, 250L).isEmpty())
    }

    @Test
    fun `seeking back inside a sentence moves the mark back`() {
        val c = following()
        c.onAudioPosition(12_500, 100L)
        assertEquals(listOf(Action.Word(p0, 0)), c.onAudioPosition(10_200, 250L))
    }

    @Test
    fun `stop and start reset the word so the next poll marks again`() {
        val c = following()
        c.onAudioPosition(10_500, 100L)
        c.stop()
        c.start(1_000L)
        assertEquals(listOf(Action.Decorate(p0), Action.Word(p0, 0)), c.onAudioPosition(10_500, 1_100L))
    }

    @Test
    fun `nothing is yielded while off`() {
        val c = ReadAlongController(points, words)
        assertTrue(c.onAudioPosition(10_500, 100L).isEmpty())
    }

    @Test
    fun `words set after the start apply from the next poll without recreating the controller`() {
        val c = following(emptyMap())
        c.onAudioPosition(10_500, 100L)
        c.setWords(words)
        assertEquals(listOf(Action.Word(p0, 0)), c.onAudioPosition(10_600, 250L))
    }

    @Test
    fun `wordStartsFor exposes the starts for the sentence`() {
        val c = following()
        assertNotNull(c.wordStartsFor(p0))
        assertEquals(listOf(10_000, 11_000, 12_000), c.wordStartsFor(p0)!!.toList())
        assertNull(c.wordStartsFor(p1))
    }

    @Test
    fun `following that resumes by itself marks the word again`() {
        val c = following()
        c.onAudioPosition(10_500, 100L)
        c.onLocatorEmitted(5_000L)
        c.onSuspectVerified(visible = false)
        assertTrue(c.onSuspectVerified(visible = true))
        assertEquals(listOf(Action.Word(p0, 0)), c.onAudioPosition(10_600, 6_000L))
    }

    @Test
    fun `while paused no Word is yielded, and back to audio marks the word again`() {
        val c = following()
        c.onAudioPosition(10_500, 100L)
        c.onLocatorEmitted(5_000L)
        c.onSuspectVerified(visible = false)
        assertTrue(c.onAudioPosition(11_500, 5_500L).isEmpty())
        val back = c.onBackToAudio(6_000L)
        assertEquals(listOf(Action.Decorate(p0), Action.Jump(p0)), back)
        assertEquals(listOf(Action.Word(p0, 1)), c.onAudioPosition(11_600, 6_150L))
    }

    // --- a word on the next page turns it (issue #841) ---------------------

    @Test
    fun `a word off the page yields one turn for the current sentence and arms the echo window`() {
        val c = following()
        c.onAudioPosition(11_500, 100L)
        assertEquals(Action.TurnForward(p0), c.onWordOffPage(p0, 200L))
        assertEquals(ReadAlongController.LocatorVerdict.Echo, c.onLocatorEmitted(300L))
    }

    @Test
    fun `a second off-page word in the same sentence does not turn again`() {
        val c = following()
        c.onAudioPosition(11_500, 100L)
        assertNotNull(c.onWordOffPage(p0, 200L))
        assertNull(c.onWordOffPage(p0, 400L))
    }

    @Test
    fun `a new sentence allows one turn again`() {
        val c = following()
        c.onAudioPosition(11_500, 100L)
        assertNotNull(c.onWordOffPage(p0, 200L))
        c.onAudioPosition(13_500, 1_000L)
        assertNotNull(c.onWordOffPage(p1, 1_100L))
    }

    @Test
    fun `no turn while paused or off`() {
        val c = following()
        c.onAudioPosition(11_500, 100L)
        c.onSuspectVerified(visible = false)
        assertNull(c.onWordOffPage(p0, 200L))
        c.stop()
        assertNull(c.onWordOffPage(p0, 300L))
    }

    @Test
    fun `a stale point yields no turn and no echo window`() {
        val c = following()
        c.onAudioPosition(13_500, 100L)
        assertNull(c.onWordOffPage(p0, 200L))
        assertEquals(ReadAlongController.LocatorVerdict.Suspect, c.onLocatorEmitted(300L))
    }

    @Test
    fun `back to audio and a restart allow a turn for the same sentence again`() {
        val c = following()
        c.onAudioPosition(11_500, 100L)
        assertNotNull(c.onWordOffPage(p0, 200L))
        c.onSuspectVerified(visible = false)
        c.onBackToAudio(5_000L)
        assertNotNull(c.onWordOffPage(p0, 5_100L))
        c.start(9_000L)
        c.onAudioPosition(11_500, 9_100L)
        assertNotNull(c.onWordOffPage(p0, 9_200L))
    }
}
