package com.booksync.ui.reader

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.security.MessageDigest

/**
 * Screen pages per reading-order resource at one layout (issue #730), as the
 * hidden [PageCounterWebView] measured them. `counts[i]` is the page count of
 * resource `i`; `chars[i]` is that resource body's text length with whitespace
 * collapsed (the same measure the web's `pageCounter.js` records), which the
 * reading-speed estimate uses. The two lists are always the same length.
 */
@Serializable
data class Counts(val counts: List<Int>, val chars: List<Int>)

/**
 * Per-book cache of [Counts] (issue #730), one JSON file per ebook at
 * `<dir>/<ebookId>.json` - in the app, `filesDir/page_counts/`. A count takes
 * seconds of off-screen layout, so it is kept per layout [key], at most
 * [MAX_KEYS] layouts per book; writing a sixth drops the least recently written.
 *
 * [read] and [write] do blocking file I/O and must not run on the main thread;
 * use [load] and [save], which hop to [ioDispatcher], from coroutine code.
 * A missing, unreadable, corrupt or wrongly shaped file reads as `null` and is
 * simply replaced by the next write - the cost of a bad file is one recount.
 */
class PageCountCache(
    private val dir: File,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) {

    @Serializable
    private data class Entry(val key: String, val counts: List<Int>, val chars: List<Int>)

    @Serializable
    private data class Book(val entries: List<Entry> = emptyList())

    /**
     * The layout key. The reader's `<html style>` (every ReadiumCSS setting,
     * including the viewport width) is long, so it is hashed; the viewport
     * size and the spine signature are kept readable for debugging.
     */
    fun key(ebookId: Int, spineSignature: String, style: String, widthPx: Int, heightPx: Int): String =
        "$ebookId:${sha1(spineSignature).take(12)}:${widthPx}x$heightPx:${sha1(style).take(16)}"

    /** Blocking. The cached counts for [key], or `null`. */
    fun read(ebookId: Int, key: String): Counts? {
        val entry = readBook(ebookId)?.entries?.lastOrNull { it.key == key } ?: return null
        if (entry.counts.size != entry.chars.size) return null
        return Counts(entry.counts, entry.chars)
    }

    /**
     * Blocking. Stores [counts] under [key] as the newest entry, dropping the
     * oldest beyond [MAX_KEYS]. Written to a temp file and renamed over the old
     * one, so a crash mid-write leaves the previous file intact.
     */
    fun write(ebookId: Int, key: String, counts: Counts) {
        val kept = readBook(ebookId)?.entries.orEmpty().filter { it.key != key }
        val entries = (kept + Entry(key, counts.counts, counts.chars)).takeLast(MAX_KEYS)
        dir.mkdirs()
        val target = fileFor(ebookId)
        val tmp = File(dir, "${target.name}.tmp")
        tmp.writeText(json.encodeToString(Book.serializer(), Book(entries)))
        if (!tmp.renameTo(target)) {
            // Windows (tests) will not rename over an existing file; Android will.
            target.delete()
            tmp.renameTo(target)
        }
    }

    /** [read] on [ioDispatcher]. */
    suspend fun load(ebookId: Int, key: String): Counts? = withContext(ioDispatcher) { read(ebookId, key) }

    /** [write] on [ioDispatcher]. */
    suspend fun save(ebookId: Int, key: String, counts: Counts) = withContext(ioDispatcher) { write(ebookId, key, counts) }

    private fun fileFor(ebookId: Int) = File(dir, "$ebookId.json")

    private fun readBook(ebookId: Int): Book? = try {
        val file = fileFor(ebookId)
        if (file.isFile) json.decodeFromString(Book.serializer(), file.readText()) else null
    } catch (e: Exception) {
        null
    }

    companion object {
        const val MAX_KEYS = 5

        private val json = Json { ignoreUnknownKeys = true }

        private fun sha1(s: String): String =
            MessageDigest.getInstance("SHA-1").digest(s.toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it) }
    }
}
