package com.booksync.ui.reader

import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The one mapping between the server's chapter number (the index into the
 * EPUB's full OPF spine) and Readium's `readingOrder` index, which leaves out
 * spine items marked `linear="no"` (issue #804).
 */
class SpineChapterMapTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun linear(href: String) = SpineItem(href, linear = true)
    private fun nonLinear(href: String) = SpineItem(href, linear = false)

    // ------------------------------------------------------------ the mapping

    @Test
    fun `a spine with no non-linear items maps every index to itself`() {
        val spine = listOf(linear("a.xhtml"), linear("b.xhtml"), linear("c.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("a.xhtml", "b.xhtml", "c.xhtml"))!!

        assertTrue(map.isIdentity)
        assertEquals(3, map.spineSize)
        for (i in 0 until 3) {
            assertEquals(i, map.readingOrderIndexOf(i))
            assertEquals(i, map.nearestReadingOrderIndex(i))
            assertEquals(i, map.spineIndexOf(i))
        }
    }

    @Test
    fun `identity is exactly today's behaviour`() {
        val map = SpineChapterMap.identity(4)
        assertTrue(map.isIdentity)
        assertEquals(4, map.spineSize)
        assertEquals(4, map.readingOrderSize)
        assertEquals(2, map.readingOrderIndexOf(2))
        assertEquals(3, map.spineIndexOf(3))
        assertNull(map.readingOrderIndexOf(4))
        assertNull(map.nearestReadingOrderIndex(-1))
    }

    @Test
    fun `leading non-linear items shift every chapter by their count`() {
        // Two cover pages, then the book: server chapter 2 is readingOrder 0.
        val spine = listOf(
            nonLinear("cover.xhtml"), nonLinear("title.xhtml"),
            linear("c1.xhtml"), linear("c2.xhtml"), linear("c3.xhtml"),
        )
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml", "c3.xhtml"))!!

        assertFalse(map.isIdentity)
        assertEquals(5, map.spineSize)
        assertEquals(3, map.readingOrderSize)
        assertEquals(0, map.readingOrderIndexOf(2))
        assertEquals(2, map.readingOrderIndexOf(4))
        assertEquals(2, map.spineIndexOf(0))
        assertEquals(4, map.spineIndexOf(2))
    }

    @Test
    fun `a non-linear item in the middle shifts only the chapters after it`() {
        val spine = listOf(linear("c1.xhtml"), nonLinear("notes.xhtml"), linear("c2.xhtml"), linear("c3.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml", "c3.xhtml"))!!

        assertEquals(0, map.readingOrderIndexOf(0))
        assertNull(map.readingOrderIndexOf(1))
        assertEquals(1, map.readingOrderIndexOf(2))
        assertEquals(2, map.readingOrderIndexOf(3))
        assertEquals(listOf(0, 2, 3), (0 until 3).map { map.spineIndexOf(it) })
    }

    @Test
    fun `trailing non-linear items change nothing before them`() {
        val spine = listOf(linear("c1.xhtml"), linear("c2.xhtml"), nonLinear("ads.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml"))!!

        assertFalse("the server counts a slot Readium does not have", map.isIdentity)
        assertEquals(0, map.readingOrderIndexOf(0))
        assertEquals(1, map.readingOrderIndexOf(1))
        assertNull(map.readingOrderIndexOf(2))
    }

    @Test
    fun `a server chapter that is itself non-linear resolves to the next linear item`() {
        val spine = listOf(
            nonLinear("cover.xhtml"), linear("c1.xhtml"), nonLinear("notes.xhtml"), linear("c2.xhtml"),
        )
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml"))!!

        assertNull("no exact item to mark or jump to", map.readingOrderIndexOf(0))
        assertEquals(0, map.nearestReadingOrderIndex(0))
        assertEquals(1, map.nearestReadingOrderIndex(2))
    }

    @Test
    fun `a trailing non-linear server chapter falls back to the last linear item`() {
        val spine = listOf(linear("c1.xhtml"), linear("c2.xhtml"), nonLinear("ads.xhtml"), nonLinear("more.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml"))!!

        assertEquals(1, map.nearestReadingOrderIndex(2))
        assertEquals(1, map.nearestReadingOrderIndex(3))
    }

    @Test
    fun `out-of-range indexes resolve to nothing in either direction`() {
        val spine = listOf(nonLinear("cover.xhtml"), linear("c1.xhtml"), linear("c2.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml"))!!

        assertNull(map.readingOrderIndexOf(-1))
        assertNull(map.readingOrderIndexOf(3))
        assertNull(map.nearestReadingOrderIndex(-1))
        assertNull(map.nearestReadingOrderIndex(3))
        assertNull(map.spineIndexOf(-1))
        assertNull(map.spineIndexOf(2))
    }

    @Test
    fun `a spine slot with no manifest entry still counts, as it does on the server`() {
        // The server keeps an empty slot for an itemref whose idref names no
        // manifest item; Readium drops it.
        val spine = listOf(SpineItem(null, linear = true), linear("c1.xhtml"), linear("c2.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml"))!!

        assertNull(map.readingOrderIndexOf(0))
        assertEquals(0, map.readingOrderIndexOf(1))
        assertEquals(1, map.spineIndexOf(0))
        assertEquals(2, map.spineIndexOf(1))
    }

    @Test
    fun `hrefs match after percent-decoding and path normalisation`() {
        val spine = listOf(nonLinear("OEBPS/cover.xhtml"), linear("OEBPS/Text/Axis Test 1.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("/OEBPS/Text/./Axis%20Test%201.xhtml#start"))!!

        assertEquals(1, map.spineIndexOf(0))
    }

    @Test
    fun `hrefs rooted differently still match by their trailing path`() {
        val spine = listOf(nonLinear("OEBPS/cover.xhtml"), linear("OEBPS/c1.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("c1.xhtml"))!!

        assertEquals(1, map.spineIndexOf(0))
    }

    @Test
    fun `a reading order that is not the spine's linear items in order cannot be mapped`() {
        val spine = listOf(linear("c1.xhtml"), linear("c2.xhtml"))
        assertNull(SpineChapterMap.build(spine, listOf("c2.xhtml", "c1.xhtml")))
        assertNull(SpineChapterMap.build(spine, listOf("c1.xhtml", "elsewhere.xhtml")))
        assertNull(SpineChapterMap.build(spine, listOf("c1.xhtml", "c2.xhtml", "c3.xhtml")))
    }

    @Test
    fun `a document listed twice maps to its linear occurrence`() {
        val spine = listOf(nonLinear("c1.xhtml"), linear("intro.xhtml"), linear("c1.xhtml"))
        val map = SpineChapterMap.build(spine, listOf("intro.xhtml", "c1.xhtml"))!!

        assertEquals(2, map.spineIndexOf(1))
    }

    // ------------------------------------------------------------ the OPF

    private val container = """
        <?xml version="1.0"?>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
          <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
        </container>
    """.trimIndent()

    private val opf = """
        <?xml version="1.0" encoding="UTF-8"?>
        <opf:package xmlns:opf="http://www.idpf.org/2007/opf" version="2.0">
          <opf:manifest>
            <opf:item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
            <opf:item id="title" href="Text/title.xhtml" media-type="application/xhtml+xml"/>
            <opf:item id="c1" href="Text/Axis%20Test_1.xhtml" media-type="application/xhtml+xml"/>
            <opf:item id="c2" href="../c2.xhtml" media-type="application/xhtml+xml"/>
          </opf:manifest>
          <opf:spine toc="ncx">
            <opf:itemref idref="cover" linear="no"/>
            <opf:itemref idref="title" linear=" NO "/>
            <opf:itemref idref="c1"/>
            <opf:itemref idref="missing"/>
            <opf:itemref idref="c2" linear="yes"/>
            <opf:itemref/>
          </opf:spine>
        </opf:package>
    """.trimIndent()

    @Test
    fun `the spine is read from the OPF the container names, one slot per itemref with an idref`() {
        val spine = OpfSpine.parse(container) { path -> if (path == "OEBPS/content.opf") opf else null }!!

        assertEquals(
            listOf(
                SpineItem("OEBPS/cover.xhtml", linear = false),
                SpineItem("OEBPS/Text/title.xhtml", linear = false),
                SpineItem("OEBPS/Text/Axis Test_1.xhtml", linear = true),
                SpineItem(null, linear = true),
                SpineItem("c2.xhtml", linear = true),
            ),
            spine,
        )
    }

    @Test
    fun `an unreadable container or OPF gives no spine`() {
        assertNull(OpfSpine.parse("<container/>") { opf })
        assertNull(OpfSpine.parse(container) { null })
    }

    private fun epub(entries: Map<String, String>): File {
        val file = tmp.newFile("book.epub")
        ZipOutputStream(file.outputStream()).use { zip ->
            for ((name, body) in entries) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(body.toByteArray())
                zip.closeEntry()
            }
        }
        return file
    }

    @Test
    fun `an EPUB file with leading non-linear items maps through its OPF`() {
        val file = epub(mapOf("META-INF/container.xml" to container, "OEBPS/content.opf" to opf))
        // What Readium's reading order holds for that OPF: the linear items
        // that have a manifest entry, hrefs percent-encoded.
        val map = SpineChapterMap.forEpub(file, listOf("OEBPS/Text/Axis%20Test_1.xhtml", "c2.xhtml"))

        assertEquals(5, map.spineSize)
        assertEquals(0, map.readingOrderIndexOf(2))
        assertEquals(1, map.readingOrderIndexOf(4))
        assertEquals(2, map.spineIndexOf(0))
        assertEquals(4, map.spineIndexOf(1))
    }

    @Test
    fun `an EPUB that cannot be mapped falls back to identity over the reading order`() {
        val notAZip = tmp.newFile("broken.epub").apply { writeText("not a zip") }
        val identity = SpineChapterMap.forEpub(notAZip, listOf("a.xhtml", "b.xhtml"))
        assertTrue(identity.isIdentity)
        assertEquals(2, identity.spineSize)

        val file = epub(mapOf("META-INF/container.xml" to container, "OEBPS/content.opf" to opf))
        val mismatched = SpineChapterMap.forEpub(file, listOf("nowhere.xhtml"))
        assertTrue(mismatched.isIdentity)
        assertEquals(1, mismatched.spineSize)
    }

    // ----------------------------------------------- the reader's call sites

    @Test
    fun `a page's server chapter counts the non-linear items before it`() {
        val spine = listOf(nonLinear("cover.xhtml"), nonLinear("title.xhtml"), linear("c1.xhtml"), linear("c2.xhtml"))
        val roHrefs = listOf("c1.xhtml", "c2.xhtml")
        val map = SpineChapterMap.build(spine, roHrefs)!!

        assertEquals(3, serverChapterForHref(map, roHrefs, "c2.xhtml"))
        // A locator matching no reading-order item is read as the first one,
        // as the reader always has — in server numbering.
        assertEquals(2, serverChapterForHref(map, roHrefs, "unknown.xhtml"))
        assertEquals(0, serverChapterForHref(SpineChapterMap.identity(0), emptyList(), "c1.xhtml"))
    }

    @Test
    fun `read-along builds its locator from the item the server's chapter names`() {
        // The reported case: two leading non-linear covers, the sync point for
        // server chapter 8. readingOrder[8] is spine item 10 — two chapters on.
        val spine = listOf(nonLinear("cover.xhtml"), nonLinear("title.xhtml")) +
            (0 until 12).map { linear("c$it.xhtml") }
        val roHrefs = (0 until 12).map { "c$it.xhtml" }
        val map = SpineChapterMap.build(spine, roHrefs)!!

        val index = map.readingOrderIndexOf(8)
        assertNotNull(index)
        assertEquals("c6.xhtml", roHrefs[index!!])
    }
}
