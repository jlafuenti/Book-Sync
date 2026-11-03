package com.booksync.ui.components

import com.booksync.data.local.SeriesEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The series line on a Library card (issues #908, #916). Normally the primary
 * series plus `+N`; while the Library is filtered to one series, that series
 * and the book's number in it, so the reader does not have to guess.
 */
class CardSeriesLabelTest {

    private val list = listOf(SeriesEntry("Axis Saga", 1.0), SeriesEntry("Axis Chronological", 6.0))

    private fun pair(series: List<SeriesEntry>) =
        BookCardVariant.Pair(id = 1, title = "Axis One", author = null, seriesList = series)

    private fun single(series: List<SeriesEntry>) = BookCardVariant.SingleMedia(
        id = 2, kind = BookCardVariant.SingleMedia.MediaKind.EBOOK,
        title = "Axis One", author = null, seriesList = series,
    )

    @Test
    fun `without a focus the label is the primary series with a plus-N`() {
        assertEquals("Axis Saga #1 +1", cardSeriesLabel(pair(list)))
        assertEquals("Axis Saga #1 +1", cardSeriesLabel(single(list), focusSeries = null))
    }

    @Test
    fun `a focused series replaces the label with that series and its number`() {
        assertEquals("Axis Chronological #6", cardSeriesLabel(pair(list), "Axis Chronological"))
        assertEquals("Axis Chronological #6", cardSeriesLabel(single(list), "axis chronological"))
    }

    @Test
    fun `a focused series the book has no number in shows the bare name`() {
        val unnumbered = listOf(SeriesEntry("Axis Saga", 1.0), SeriesEntry("Axis Chronological"))
        assertEquals("Axis Chronological", cardSeriesLabel(pair(unnumbered), "Axis Chronological"))
    }

    @Test
    fun `a focus the book is not in falls back to the primary label`() {
        assertEquals("Axis Saga #1 +1", cardSeriesLabel(pair(list), "Elsewhere"))
    }

    @Test
    fun `stacks and series-less cards have no label`() {
        val stack = BookCardVariant.SeriesStack("Axis Saga", null, 1, 1, 0, 0)
        assertNull(cardSeriesLabel(stack, "Axis Saga"))
        assertNull(cardSeriesLabel(pair(emptyList()), "Axis Saga"))
    }
}
