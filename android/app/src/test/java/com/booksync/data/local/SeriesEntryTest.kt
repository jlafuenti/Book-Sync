package com.booksync.data.local

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * A book can belong to several series (issue #908). The entries are stored as
 * one JSON column and shown through the helpers in `SeriesEntry.kt`; the label
 * and canonical-string formats match the server and web (`Name #4; Other #2.5`).
 */
class SeriesEntryTest {

    private val saga = SeriesEntry("Axis Saga", 4.0)
    private val chrono = SeriesEntry("Axis Chronological", 2.5)

    @Test
    fun `an empty list encodes to null so the column stays null`() {
        assertNull(encodeSeries(emptyList()))
    }

    @Test
    fun `entries round trip in order`() {
        val list = listOf(saga, chrono, SeriesEntry("Axis Unnumbered"))
        assertEquals(list, decodeSeries(encodeSeries(list)))
    }

    @Test
    fun `the stored shape is a JSON array of name and index`() {
        assertEquals("""[{"name":"Axis Saga","index":3.0}]""", encodeSeries(listOf(SeriesEntry("Axis Saga", 3.0))))
    }

    @Test
    fun `null, blank and malformed JSON decode to no series`() {
        assertEquals(emptyList<SeriesEntry>(), decodeSeries(null))
        assertEquals(emptyList<SeriesEntry>(), decodeSeries(""))
        assertEquals(emptyList<SeriesEntry>(), decodeSeries("not json"))
        assertEquals(emptyList<SeriesEntry>(), decodeSeries("""{"name":"x"}"""))
    }

    @Test
    fun `decoding drops entries with a blank name`() {
        val json = """[{"name":" ","index":1.0},{"name":"Axis Saga"}]"""
        assertEquals(listOf(SeriesEntry("Axis Saga")), decodeSeries(json))
    }

    @Test
    fun `primary is the first entry`() {
        assertEquals(saga, listOf(saga, chrono).primary())
        assertNull(emptyList<SeriesEntry>().primary())
    }

    @Test
    fun `an index prints without a trailing zero`() {
        assertEquals("Axis Saga #4", formatSeriesEntry(saga))
        assertEquals("Axis Chronological #2.5", formatSeriesEntry(chrono))
        assertEquals("Axis Saga", formatSeriesEntry(SeriesEntry("Axis Saga")))
    }

    @Test
    fun `float noise in a stored index does not reach the screen`() {
        assertEquals("Axis Saga #2.1", formatSeriesEntry(SeriesEntry("Axis Saga", 2.0999999046325684)))
    }

    @Test
    fun `the canonical list joins entries with a semicolon`() {
        assertEquals("Axis Saga #4; Axis Chronological #2.5", formatSeriesList(listOf(saga, chrono)))
        assertEquals("", formatSeriesList(emptyList()))
    }

    @Test
    fun `the card label is the primary series plus a count of the rest`() {
        assertNull(seriesLabel(emptyList()))
        assertEquals("Axis Saga #4", seriesLabel(listOf(saga)))
        assertEquals("Axis Saga #4 +1", seriesLabel(listOf(saga, chrono)))
        assertEquals("Axis Saga #4 +2", seriesLabel(listOf(saga, chrono, SeriesEntry("Third"))))
    }

    @Test
    fun `union is the ebook's entries then the audiobook's new names`() {
        val ebook = listOf(SeriesEntry("Axis Saga", 4.0))
        val audio = listOf(SeriesEntry("axis saga", 9.0), SeriesEntry("Axis Chronological", 2.5))
        assertEquals(
            listOf(SeriesEntry("Axis Saga", 4.0), SeriesEntry("Axis Chronological", 2.5)),
            unionSeries(ebook, audio),
        )
    }

    @Test
    fun `union with an empty side is the other side`() {
        assertEquals(listOf(saga), unionSeries(emptyList(), listOf(saga)))
        assertEquals(listOf(saga), unionSeries(listOf(saga), emptyList()))
        assertEquals(emptyList<SeriesEntry>(), unionSeries(emptyList(), emptyList()))
    }

    @Test
    fun `union dedupes a repeated name inside one side too`() {
        assertEquals(listOf(saga), unionSeries(listOf(saga, SeriesEntry("AXIS SAGA", 7.0)), emptyList()))
    }

    @Test
    fun `names match ignoring case and outer spaces`() {
        assertEquals(true, seriesNameMatches("  axis saga ", "Axis Saga"))
        assertEquals(false, seriesNameMatches("Axis", "Axis Saga"))
        assertEquals(false, seriesNameMatches(null, "Axis Saga"))
    }

    @Test
    fun `indexIn finds the index inside one named series`() {
        assertEquals(2.5, listOf(saga, chrono).indexIn("axis chronological"))
        assertNull(listOf(saga, chrono).indexIn("Elsewhere"))
    }
}
