package com.booksync.data.local

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.SeriesEntryDto
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/**
 * One series a book belongs to (issue #908). A book carries a list of these,
 * the first being its primary series. The server sends them as
 * `series_list: [{name, index}]`; Room stores them as one JSON column
 * (`seriesJson`) on `ebooks`, `audiobooks` and `book_pairs`.
 *
 * Formats match the server and web: the canonical string is
 * `Axis Saga #4; Axis Chronological #2.5`, and a card shows the primary series
 * with a `+N` for the rest.
 */
@Serializable
data class SeriesEntry(val name: String, val index: Double? = null)

private val seriesJson = Json { ignoreUnknownKeys = true; coerceInputValues = true }
private val seriesListSerializer = ListSerializer(SeriesEntry.serializer())

/** The `seriesJson` column value for [list]; null when empty so "no series" stays NULL. */
fun encodeSeries(list: List<SeriesEntry>): String? =
    if (list.isEmpty()) null else seriesJson.encodeToString(seriesListSerializer, list)

/** Inverse of [encodeSeries]. Null, blank or malformed JSON is "no series", never a crash. */
fun decodeSeries(json: String?): List<SeriesEntry> {
    if (json.isNullOrBlank()) return emptyList()
    return try {
        seriesJson.decodeFromString(seriesListSerializer, json).filter { it.name.isNotBlank() }
    } catch (_: Exception) {
        emptyList()
    }
}

fun List<SeriesEntry>.primary(): SeriesEntry? = firstOrNull()

/** Case- and outer-space-insensitive series name equality (the Library filter's rule). */
fun seriesNameMatches(a: String?, b: String?): Boolean =
    a != null && b != null && a.trim().equals(b.trim(), ignoreCase = true)

/** The index this list gives to the series called [name], or null. */
fun List<SeriesEntry>.indexIn(name: String): Double? =
    firstOrNull { seriesNameMatches(it.name, name) }?.index

/** `4`, not `4.0`; and no float noise such as `2.0999999` from a 32-bit REAL. */
fun formatSeriesIndex(index: Double): String {
    val rounded = Math.round(index * 10_000.0) / 10_000.0
    return if (rounded % 1.0 == 0.0) rounded.toLong().toString() else rounded.toString()
}

/** ` #4` for an entry with an index, empty without one; the part after the name. */
fun seriesIndexSuffix(entry: SeriesEntry): String =
    entry.index?.let { " #${formatSeriesIndex(it)}" }.orEmpty()

/** `Axis Saga #4`, or the bare name when the entry has no index. */
fun formatSeriesEntry(entry: SeriesEntry): String = entry.name.trim() + seriesIndexSuffix(entry)

/** The canonical string: entries joined by `"; "`, empty for no series. */
fun formatSeriesList(list: List<SeriesEntry>): String =
    list.joinToString("; ") { formatSeriesEntry(it) }

/** Card label: the primary series, plus ` +N` when the book is in more. Null for no series. */
fun seriesLabel(list: List<SeriesEntry>): String? {
    val primary = list.primary() ?: return null
    val more = list.size - 1
    return formatSeriesEntry(primary) + if (more > 0) " +$more" else ""
}

/**
 * A pair's series: the ebook's entries, then the audiobook's names not already
 * present (case-insensitive), the ebook's index winning. The same rule as the
 * server's pair browse and the web's `seriesOf`.
 */
fun unionSeries(ebook: List<SeriesEntry>, audiobook: List<SeriesEntry>): List<SeriesEntry> {
    val out = mutableListOf<SeriesEntry>()
    for (entry in ebook + audiobook) {
        if (entry.name.isBlank()) continue
        if (out.none { seriesNameMatches(it.name, entry.name) }) out += entry
    }
    return out
}

/** The decoded `seriesJson` column of each entity; primary series first. */
val BookPairEntity.seriesList: List<SeriesEntry> get() = decodeSeries(seriesJson)
val EBookEntity.seriesList: List<SeriesEntry> get() = decodeSeries(seriesJson)
val AudioBookEntity.seriesList: List<SeriesEntry> get() = decodeSeries(seriesJson)

/** The server's `series_list`, as the entries Room stores. */
fun List<SeriesEntryDto>.toSeriesEntries(): List<SeriesEntry> =
    mapNotNull { dto -> dto.name.trim().takeIf { it.isNotEmpty() }?.let { SeriesEntry(it, dto.index) } }
