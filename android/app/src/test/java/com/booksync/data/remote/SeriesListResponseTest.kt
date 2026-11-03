package com.booksync.data.remote

import com.booksync.data.local.SeriesEntry
import com.booksync.data.local.toSeriesEntries
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The library responses carry `series_list: [{name, index}]` instead of a
 * scalar `series` / `series_index` (issue #908). The app's Json ignores unknown
 * keys and coerces nulls, so a server that has not been updated (an old scalar
 * payload) must read as "no series", never as a failed library refresh.
 */
class SeriesListResponseTest {

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    private val ebookBase =
        """"id": 1, "title": "Axis Test", "filename": "a.epub", "format": "epub", "uploaded_at": "2026-01-01T00:00:00""""

    @Test
    fun `an ebook's series list decodes in order with its indexes`() {
        val ebook = json.decodeFromString<EBookResponse>(
            """{$ebookBase, "series_list": [{"name": "Axis Saga", "index": 4}, {"name": "Axis Chronological", "index": 2.5}, {"name": "Unnumbered", "index": null}]}""",
        )

        assertEquals(
            listOf(SeriesEntry("Axis Saga", 4.0), SeriesEntry("Axis Chronological", 2.5), SeriesEntry("Unnumbered", null)),
            ebook.series_list.toSeriesEntries(),
        )
    }

    @Test
    fun `an empty series list is no series`() {
        val ebook = json.decodeFromString<EBookResponse>("""{$ebookBase, "series_list": []}""")

        assertEquals(emptyList<SeriesEntryDto>(), ebook.series_list)
    }

    @Test
    fun `a server that only sends the old scalar series reads as no series`() {
        val ebook = json.decodeFromString<EBookResponse>(
            """{$ebookBase, "series": "Axis Saga", "series_index": 3.0}""",
        )

        assertEquals(emptyList<SeriesEntryDto>(), ebook.series_list)
    }

    @Test
    fun `audiobook and metadata responses carry the list too`() {
        val audio = json.decodeFromString<AudioBookResponse>(
            """{"id": 2, "title": "A", "filename": "a.m4b", "format": "m4b", "uploaded_at": "2026-01-01T00:00:00", "series_list": [{"name": "Axis Saga", "index": 1}]}""",
        )
        val meta = json.decodeFromString<BookMetadataResponse>(
            """{"id": 3, "title": "A", "series_list": [{"name": "Axis Saga", "index": 1}]}""",
        )

        assertEquals(listOf(SeriesEntry("Axis Saga", 1.0)), audio.series_list.toSeriesEntries())
        assertEquals(listOf(SeriesEntry("Axis Saga", 1.0)), meta.series_list.toSeriesEntries())
    }

    @Test
    fun `entries with a blank name are dropped and names are trimmed`() {
        val entries = listOf(SeriesEntryDto("  ", 1.0), SeriesEntryDto(" Axis Saga ", 2.0)).toSeriesEntries()

        assertEquals(listOf(SeriesEntry("Axis Saga", 2.0)), entries)
    }
}
