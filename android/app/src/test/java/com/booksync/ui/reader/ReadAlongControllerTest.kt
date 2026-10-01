package com.booksync.ui.reader

import com.booksync.data.local.entity.SyncPointEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReadAlongControllerTest {

    private fun point(chapter: Int, sentence: Int, startMs: Int, endMs: Int, confidence: Float = 1f) =
        SyncPointEntity(
            bookPairId = 1, epubChapter = chapter, epubSentenceIndex = sentence,
            epubTextPreview = "Sentence $chapter.$sentence of Axis Test.",
            audioStartMs = startMs, audioEndMs = endMs, confidence = confidence,
        )

    private val points = listOf(
        point(2, 0, 10_000, 13_000),
        point(2, 1, 13_000, 16_000, confidence = 0f),
        point(3, 0, 16_000, 20_000),
    )

    private fun following(now: Long = 0L) = ReadAlongController(points).apply { start(now) }

    @Test
    fun `starts Off and does nothing until started`() {
        val c = ReadAlongController(points)
        assertEquals(ReadAlongController.State.Off, c.state)
        assertNull(c.onAudioPosition(11_000, 0L))
        assertEquals(ReadAlongController.LocatorVerdict.Ignored, c.onLocatorEmitted(0L))
    }

    @Test
    fun `first audio position decorates the matched sentence`() {
        val c = following()
        val action = c.onAudioPosition(11_000, 100L)
        assertEquals(points[0], action?.point)
        assertEquals(points[0], c.currentPoint)
    }

    @Test
    fun `same sentence again decorates nothing`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        assertNull(c.onAudioPosition(12_500, 600L))
    }

    @Test
    fun `interpolated sentences are decorated like any other`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        val action = c.onAudioPosition(14_000, 600L)
        assertEquals(points[1], action?.point)
        assertEquals(0f, action!!.point.confidence)
    }

    @Test
    fun `a position before every point resolves to the first point`() {
        val c = following()
        assertEquals(points[0], c.onAudioPosition(0, 100L)?.point)
    }

    @Test
    fun `empty sync map decorates nothing`() {
        val c = ReadAlongController(emptyList()).apply { start(0L) }
        assertNull(c.onAudioPosition(11_000, 100L))
    }

    @Test
    fun `a sentence that is not visible produces a jump and arms the echo window`() {
        val c = following()
        val p = c.onAudioPosition(11_000, 100L)!!.point
        val jump = c.onSentenceVisibility(p, visible = false, nowMs = 150L)
        assertEquals(p, jump?.point)
        assertEquals(ReadAlongController.LocatorVerdict.Echo, c.onLocatorEmitted(900L))
        assertTrue(c.isFollowing)
    }

    @Test
    fun `a visible sentence produces no jump`() {
        val c = following()
        val p = c.onAudioPosition(11_000, 100L)!!.point
        assertNull(c.onSentenceVisibility(p, visible = true, nowMs = 150L))
    }

    @Test
    fun `a stale visibility answer for a superseded sentence produces no jump`() {
        val c = following()
        val first = c.onAudioPosition(11_000, 100L)!!.point
        c.onAudioPosition(14_000, 600L)
        assertNull(c.onSentenceVisibility(first, visible = false, nowMs = 650L))
    }

    @Test
    fun `a locator emission outside the echo window is only a suspect and keeps following`() {
        val c = following()
        val p = c.onAudioPosition(11_000, 100L)!!.point
        c.onSentenceVisibility(p, visible = false, nowMs = 150L)
        assertEquals(ReadAlongController.LocatorVerdict.Suspect, c.onLocatorEmitted(5_000L))
        assertTrue(c.isFollowing)
    }

    @Test
    fun `a confirmed manual turn pauses following`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        assertEquals(ReadAlongController.LocatorVerdict.Suspect, c.onLocatorEmitted(5_000L))
        c.confirmManualTurn()
        assertEquals(ReadAlongController.State.Paused, c.state)
        assertTrue(c.isPaused)
        assertFalse(c.isFollowing)
        assertEquals(ReadAlongController.LocatorVerdict.Ignored, c.onLocatorEmitted(6_000L))
    }

    @Test
    fun `confirming a manual turn while off or paused changes nothing`() {
        val c = ReadAlongController(points)
        c.confirmManualTurn()
        assertEquals(ReadAlongController.State.Off, c.state)
        c.start(0L)
        c.confirmManualTurn()
        c.confirmManualTurn()
        assertEquals(ReadAlongController.State.Paused, c.state)
    }

    @Test
    fun `quoteFor uses the first non-blank line of a preview`() {
        assertEquals("“His mother …”", ReadAlongController.quoteFor("“His mother …”\n“Human,” River Shoulders said."))
        assertEquals("Plain sentence.", ReadAlongController.quoteFor("  Plain sentence.  "))
        assertEquals("Second", ReadAlongController.quoteFor("\n  \nSecond\nThird"))
        assertNull(ReadAlongController.quoteFor(null))
        assertNull(ReadAlongController.quoteFor("   \n "))
    }

    @Test
    fun `while paused audio positions are tracked but not decorated`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        c.onLocatorEmitted(5_000L)
        c.confirmManualTurn()
        assertNull(c.onAudioPosition(17_000, 5_500L))
        assertEquals(points[2], c.currentPoint)
    }

    @Test
    fun `back to audio resumes following, decorates and jumps to the current sentence`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        c.onLocatorEmitted(5_000L)
        c.confirmManualTurn()
        c.onAudioPosition(17_000, 5_500L)
        val actions = c.onBackToAudio(6_000L)
        assertEquals(
            listOf(
                ReadAlongController.Action.Decorate(points[2]),
                ReadAlongController.Action.Jump(points[2]),
            ),
            actions,
        )
        assertTrue(c.isFollowing)
        assertEquals(ReadAlongController.LocatorVerdict.Echo, c.onLocatorEmitted(6_500L))
    }

    @Test
    fun `back to audio while following or off does nothing`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        assertTrue(c.onBackToAudio(200L).isEmpty())
        c.stop()
        assertTrue(c.onBackToAudio(300L).isEmpty())
        assertEquals(ReadAlongController.State.Off, c.state)
    }

    @Test
    fun `stop clears the current sentence and ignores locators`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        c.stop()
        assertNull(c.currentPoint)
        assertEquals(ReadAlongController.LocatorVerdict.Ignored, c.onLocatorEmitted(200L))
    }

    @Test
    fun `restart after stop decorates the first position again`() {
        val c = following()
        c.onAudioPosition(11_000, 100L)
        c.stop()
        c.start(1_000L)
        assertEquals(points[0], c.onAudioPosition(11_000, 1_100L)?.point)
    }
}
