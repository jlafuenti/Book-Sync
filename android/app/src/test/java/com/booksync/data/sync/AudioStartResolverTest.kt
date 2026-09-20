package com.booksync.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The audio-start ladder's ordering (issue #643) — where playback begins for a
 * book, before any sync map is consulted.
 *
 * The reported failure: read the ebook, get in the car, pick the book, and
 * Android Auto starts wherever the audiobook was last played. These pin the
 * decision that stops it, and the guards that stop the cure being worse than
 * the disease.
 */
class AudioStartResolverTest {

    private fun position(
        source: String = SOURCE_EBOOK,
        audioPositionMs: Int? = null,
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        epubTextPreview: String? = null,
    ) = StoredPosition(
        source = source,
        epubChapter = epubChapter,
        epubSentenceIndex = epubSentenceIndex,
        epubTextPreview = epubTextPreview,
        audioPositionMs = audioPositionMs,
    )

    private fun kinds(p: StoredPosition) = planAudioStart(p).map { it.kind }

    // --- The reported bug ---------------------------------------------------

    @Test
    fun `a reading position outranks the stale audio position it was saved beside`() {
        // The exact shape of the bug: the row holds a current ebook anchor and
        // an audio position left over from the last time the book was played.
        val steps = planAudioStart(
            position(
                source = SOURCE_EBOOK,
                audioPositionMs = 3_600_000,
                epubChapter = 4,
                epubSentenceIndex = 12,
                epubTextPreview = "the text on the page the reader had reached",
            )
        )

        assertEquals(listOf("text", "sentence", "stored"), steps.map { it.kind })
    }

    @Test
    fun `listening keeps its own position and never derives over it`() {
        // Once the user actually plays, the first claiming save stamps
        // `audiobook` and the real audio ms. From then on the derivation must
        // stop, or every resume would drag playback back to the page.
        val steps = planAudioStart(
            position(
                source = SOURCE_AUDIOBOOK,
                audioPositionMs = 3_600_000,
                epubChapter = 4,
                epubSentenceIndex = 12,
                epubTextPreview = "the text on the page the reader had reached",
            )
        )

        assertEquals("stored", steps.first().kind)
        assertEquals(3_600_000, (steps.first() as AudioStartStep.Stored).audioPositionMs)
    }

    // --- Falling through ----------------------------------------------------

    @Test
    fun `a failed derivation falls back to the stored position, not to zero`() {
        // A stale listening position is a worse answer than the page; it is a
        // far better one than the beginning of the book.
        assertEquals(
            listOf("text", "sentence", "stored"),
            kinds(
                position(
                    audioPositionMs = 3_600_000,
                    epubChapter = 4,
                    epubTextPreview = "a preview long enough to search for",
                )
            ),
        )
    }

    @Test
    fun `an audiobook record with no usable audio position still derives`() {
        // A first-ever write claims `audiobook` even when nothing was played
        // (see savePlaybackPosition's claimFormat escalation), and a record
        // sitting at 0 has nothing to offer.
        assertEquals(
            listOf("text", "sentence"),
            kinds(
                position(
                    source = SOURCE_AUDIOBOOK,
                    audioPositionMs = 0,
                    epubChapter = 4,
                    epubTextPreview = "a preview long enough to search for",
                )
            ),
        )
    }

    @Test
    fun `a record with nothing to offer plans no steps`() {
        assertTrue(planAudioStart(position()).isEmpty())
        assertTrue(planAudioStart(position(source = SOURCE_AUDIOBOOK)).isEmpty())
    }

    // --- Guards -------------------------------------------------------------

    @Test
    fun `a preview too short to place is not offered`() {
        // Below MIN_SEARCHABLE_PREVIEW the text matches too much to locate —
        // the same threshold the reader's text rung applies.
        val steps = kinds(position(epubChapter = 4, epubTextPreview = "short"))

        assertEquals(listOf("sentence"), steps)
    }

    @Test
    fun `no chapter means no sentence rung, because the index alone places nothing`() {
        val steps = kinds(position(epubSentenceIndex = 12, audioPositionMs = 500))

        assertEquals(listOf("stored"), steps)
    }

    @Test
    fun `the sentence rung carries its chapter so the executor can stay inside it`() {
        // The stored sentence index may belong to a different chapter than the
        // stored chapter (issue #644), so the executor must not walk backwards
        // across chapters the way the server's epub_to_audio does.
        val step = planAudioStart(position(epubChapter = 4, epubSentenceIndex = 200))
            .filterIsInstance<AudioStartStep.Sentence>()
            .single()

        assertEquals(4, step.chapter)
        assertEquals(200, step.sentenceIndex)
    }

    @Test
    fun `a chapter with no sentence index is still worth a rung`() {
        // The chapter alone places the top of the chapter, which is a real
        // answer — and it is what a save that never matched the map leaves.
        val step = planAudioStart(position(epubChapter = 4))
            .filterIsInstance<AudioStartStep.Sentence>()
            .single()

        assertEquals(4, step.chapter)
        assertEquals(null, step.sentenceIndex)
    }

    @Test
    fun `a negative or zero stored position is not offered as a rung`() {
        assertTrue(kinds(position(audioPositionMs = 0)).isEmpty())
        assertTrue(kinds(position(audioPositionMs = -1)).isEmpty())
    }

    @Test
    fun `the text rung seeds the search with the stored chapter`() {
        val step = planAudioStart(
            position(epubChapter = 7, epubTextPreview = "a preview long enough to search for")
        ).filterIsInstance<AudioStartStep.Text>().single()

        assertEquals(7, step.seedChapter)
        assertEquals("a preview long enough to search for", step.text)
    }

    @Test
    fun `an unrecognised source is treated as reading, not as listening`() {
        // Anything that is not an explicit audiobook claim gets the ebook
        // order: deriving and being wrong costs a seek, while trusting a stale
        // audio position is the bug this ladder exists to fix.
        assertEquals(
            listOf("text", "sentence", "stored"),
            kinds(
                position(
                    source = "something-else",
                    audioPositionMs = 3_600_000,
                    epubChapter = 4,
                    epubTextPreview = "a preview long enough to search for",
                )
            ),
        )
    }
}
