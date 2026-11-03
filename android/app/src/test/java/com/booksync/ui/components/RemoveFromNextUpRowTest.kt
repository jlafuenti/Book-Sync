package com.booksync.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * "Remove from Next up" (issue #923) is offered only where the caller wired it,
 * which is Home's Next up carousel and nowhere else. The sheet is a Composable
 * (excluded from Kover), so the decision lives in [offersRemoveFromNextUp].
 */
class RemoveFromNextUpRowTest {

    @Test
    fun `the row is offered only when the callback is set`() {
        assertFalse(offersRemoveFromNextUp(OverflowActions()))
        assertTrue(offersRemoveFromNextUp(OverflowActions(onRemoveFromNextUp = {})))
    }

    @Test
    fun `a card outside Next up carries no series, on every book variant`() {
        val pair = OverflowTarget.Pair(
            pairId = 1, title = "Axis One", subtitle = null, hasEbookDownloaded = true,
            hasAudiobookDownloaded = true, isTranscribed = true, isQueuedOrTranscribing = false,
            isComplete = false,
        )
        val ebook = OverflowTarget.Ebook(ebookId = 1, title = "Axis One", subtitle = null, isDownloaded = true, isPaired = false)
        val audiobook = OverflowTarget.Audiobook(audiobookId = 1, title = "Axis One", subtitle = null, isDownloaded = true, isPaired = false)
        listOf(pair, ebook, audiobook).forEach { assertNull(it.nextUpSeries) }
        assertEquals("Axis Saga", pair.copy(nextUpSeries = "Axis Saga").nextUpSeries)
        assertEquals("Axis Saga", ebook.copy(nextUpSeries = "Axis Saga").nextUpSeries)
        assertEquals("Axis Saga", audiobook.copy(nextUpSeries = "Axis Saga").nextUpSeries)
    }
}
