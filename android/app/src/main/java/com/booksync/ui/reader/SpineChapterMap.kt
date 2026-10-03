package com.booksync.ui.reader

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipFile
import org.jsoup.Jsoup
import org.jsoup.nodes.Element
import org.jsoup.parser.Parser

/**
 * One `<itemref>` of an EPUB's OPF spine: the document it names, as a path
 * from the root of the archive (decoded, no fragment), or null when the
 * itemref's idref has no manifest entry; and whether it is in the linear
 * reading order (`linear="no"` is not).
 */
data class SpineItem(val href: String?, val linear: Boolean)

/**
 * The one mapping between the two chapter numberings the reader deals in
 * (issue #804).
 *
 * - The **spine index** is an itemref's position in the full OPF spine. It is
 *   what the server's parser numbers chapters by, so every `epub_chapter` the
 *   server stores or sends (sync points, positions) uses it, and so does the
 *   web reader (epub.js `book.spine.items` holds every itemref).
 * - The **reading-order index** is a position in Readium's
 *   `publication.readingOrder`, which leaves out itemrefs marked
 *   `linear="no"` (and any whose idref names no manifest item).
 *
 * For a book with N such items before a chapter the two differ by N. Inside
 * the reader everything stays in reading-order indexes; this class is used
 * only where a chapter crosses to or from something server-shaped, so nothing
 * is translated twice. A book without such items gets [identity], which is
 * exactly the behaviour from before the map existed.
 */
class SpineChapterMap private constructor(
    /** Reading-order index per spine index; -1 for an item Readium leaves out. */
    private val readingOrderBySpine: IntArray,
    /** Spine index per reading-order index, strictly increasing. */
    private val spineByReadingOrder: IntArray,
) {
    /** Number of spine items: the server's chapter count. */
    val spineSize: Int get() = readingOrderBySpine.size

    /** Number of reading-order items. */
    val readingOrderSize: Int get() = spineByReadingOrder.size

    /** True when the two numberings are the same. */
    val isIdentity: Boolean =
        readingOrderBySpine.size == spineByReadingOrder.size &&
            spineByReadingOrder.withIndex().all { (i, s) -> i == s }

    /**
     * The reading-order item for server chapter [spineIndex], or null when
     * that spine item is not in the reading order (non-linear) or the index is
     * out of range. For callers that must show *that* document and nothing
     * else — read-along marking a sentence.
     */
    fun readingOrderIndexOf(spineIndex: Int): Int? =
        readingOrderBySpine.getOrNull(spineIndex)?.takeIf { it >= 0 }

    /**
     * The reading-order item closest to server chapter [spineIndex], for
     * callers that need *somewhere* to open: the item itself when it is
     * linear, else the next linear item after it, else (a trailing
     * non-linear run) the last linear item. Null only for an out-of-range
     * index or an empty reading order.
     *
     * Next rather than previous because non-linear items are overwhelmingly
     * covers, title pages and other front matter, and the reader's own
     * continuation past them is the next linear document.
     */
    fun nearestReadingOrderIndex(spineIndex: Int): Int? {
        if (spineIndex !in 0 until spineSize || readingOrderSize == 0) return null
        readingOrderIndexOf(spineIndex)?.let { return it }
        val next = spineByReadingOrder.indexOfFirst { it > spineIndex }
        return if (next >= 0) next else readingOrderSize - 1
    }

    /** The server chapter for reading-order item [readingOrderIndex], or null when out of range. */
    fun spineIndexOf(readingOrderIndex: Int): Int? = spineByReadingOrder.getOrNull(readingOrderIndex)

    override fun toString(): String =
        if (isIdentity) "SpineChapterMap(identity, $spineSize)"
        else "SpineChapterMap(spine=$spineSize, readingOrder=${spineByReadingOrder.joinToString(",")})"

    companion object {
        /** Both numberings the same: a book with no non-linear items, or one that could not be mapped. */
        fun identity(size: Int): SpineChapterMap {
            val indexes = IntArray(size.coerceAtLeast(0)) { it }
            return SpineChapterMap(indexes, indexes.copyOf())
        }

        /**
         * Map [spine] onto Readium's [readingOrderHrefs] by href. Each
         * reading-order item is matched, in order, to the next linear spine
         * item naming the same document, so a document listed twice maps to
         * its linear occurrence. Null when the reading order is not the
         * spine's linear items in order — something this class does not
         * understand, which the caller treats as identity.
         */
        fun build(spine: List<SpineItem>, readingOrderHrefs: List<String>): SpineChapterMap? {
            val bySpine = IntArray(spine.size) { -1 }
            val byReadingOrder = IntArray(readingOrderHrefs.size)
            var next = 0
            for ((ro, rawHref) in readingOrderHrefs.withIndex()) {
                val href = normalizeEpubPath(rawHref)
                var found = -1
                while (next < spine.size) {
                    val item = spine[next++]
                    val itemHref = item.href
                    if (item.linear && itemHref != null && sameDocument(itemHref, href)) {
                        found = next - 1
                        break
                    }
                }
                if (found < 0) return null
                bySpine[found] = ro
                byReadingOrder[ro] = found
            }
            return SpineChapterMap(bySpine, byReadingOrder)
        }

        /**
         * The map for the EPUB at [file], whose Readium reading order is
         * [readingOrderHrefs]. Reads the OPF straight from the archive, the way
         * the server's parser does. Anything that cannot be read or matched
         * falls back to [identity] over the reading order — the numbering the
         * reader used before this map existed — so a strange book is never
         * worse off than it was.
         *
         * Blocking: reads the archive. Call it off the main thread.
         */
        fun forEpub(file: File, readingOrderHrefs: List<String>): SpineChapterMap {
            val spine = try {
                ZipFile(file).use { zip ->
                    fun read(path: String): String? =
                        zip.getEntry(path)?.let { entry -> zip.getInputStream(entry).use { String(it.readBytes(), Charsets.UTF_8) } }
                    val container = read("META-INF/container.xml") ?: return@use null
                    OpfSpine.parse(container, ::read)
                }
            } catch (e: Exception) {
                null
            }
            return spine?.let { build(it, readingOrderHrefs) } ?: identity(readingOrderHrefs.size)
        }

        /** The same document, also when one href is rooted further up than the other. */
        private fun sameDocument(a: String, b: String): Boolean =
            a == b || (b.isNotEmpty() && a.endsWith("/$b")) || (a.isNotEmpty() && b.endsWith("/$a"))
    }
}

/**
 * The OPF spine of an EPUB, read the way the server's
 * `_extract_epub_documents_via_zip` reads it: the first `rootfile` named by
 * `META-INF/container.xml`, then one slot per `itemref` that has an idref,
 * hrefs resolved against the OPF's directory. Namespace prefixes are ignored,
 * as the server's `local-name()` does.
 */
object OpfSpine {

    /**
     * Parse [containerXml], then the OPF it names through [readEntry] (a path
     * from the root of the archive to that entry's text, or null). Null when
     * either cannot be found.
     */
    fun parse(containerXml: String, readEntry: (String) -> String?): List<SpineItem>? {
        val opfPath = xml(containerXml).elementsNamed("rootfile")
            .map { it.attr("full-path") }
            .firstOrNull { it.isNotEmpty() }
            ?: return null
        val opf = readEntry(opfPath)?.let(::xml) ?: return null
        val opfDir = opfPath.substringBeforeLast('/', missingDelimiterValue = "")

        val manifest = opf.elementsNamed("item")
            .filter { it.attr("id").isNotEmpty() && it.attr("href").isNotEmpty() }
            .associate { it.attr("id") to it.attr("href") }

        return opf.elementsNamed("itemref")
            .filter { it.attr("idref").isNotEmpty() }
            .map { ref ->
                val href = manifest[ref.attr("idref")]
                SpineItem(
                    href = href?.let { normalizeEpubPath(if (opfDir.isEmpty()) it else "$opfDir/$it") },
                    linear = !ref.attr("linear").trim().equals("no", ignoreCase = true),
                )
            }
    }

    private fun xml(text: String) = Jsoup.parse(text, "", Parser.xmlParser())

    private fun Element.elementsNamed(localName: String): List<Element> =
        getAllElements().filter { it.tagName().substringAfterLast(':').equals(localName, ignoreCase = true) }
}

/**
 * An EPUB-internal href as a plain path from the root of the archive: no
 * fragment or query, percent-decoded, `.` and `..` resolved, no leading `/`.
 * Applied to both the OPF's hrefs and Readium's, so they compare equal.
 */
internal fun normalizeEpubPath(href: String): String {
    val path = percentDecode(href.substringBefore('#').substringBefore('?'))
    val parts = ArrayDeque<String>()
    for (segment in path.split('/')) {
        when (segment) {
            "", "." -> Unit
            ".." -> parts.removeLastOrNull()
            else -> parts.addLast(segment)
        }
    }
    return parts.joinToString("/")
}

/** `%XX` sequences decoded as UTF-8; a `+` stays a `+` (this is a path, not a form). */
private fun percentDecode(s: String): String {
    if ('%' !in s) return s
    val out = ByteArrayOutputStream()
    var i = 0
    while (i < s.length) {
        val c = s[i]
        val hi = if (c == '%') s.getOrNull(i + 1)?.digitToIntOrNull(16) else null
        val lo = if (hi != null) s.getOrNull(i + 2)?.digitToIntOrNull(16) else null
        if (hi != null && lo != null) {
            out.write(hi * 16 + lo)
            i += 3
        } else {
            out.write(c.toString().toByteArray(Charsets.UTF_8))
            i++
        }
    }
    return out.toString(Charsets.UTF_8.name())
}
