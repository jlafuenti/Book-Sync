package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Issue #730, task 12. The pure inputs [ReaderActivity] hands the indicator and the page counter. */
class ReaderProgressInputsTest {

    private val spine = listOf("OEBPS/text/ch1.xhtml", "OEBPS/text/ch2.xhtml", "OEBPS/text/ch3.xhtml")

    // ---- sectionIndexOf ----

    @Test
    fun `a locator href resolves to its reading order index`() {
        assertEquals(1, ReaderProgressInputs.sectionIndexOf(spine, "OEBPS/text/ch2.xhtml"))
    }

    @Test
    fun `a locator href differing only in its folder falls back to the file name rule`() {
        assertEquals(2, ReaderProgressInputs.sectionIndexOf(spine, "text/ch3.xhtml"))
    }

    @Test
    fun `an href outside the reading order is -1`() {
        assertEquals(-1, ReaderProgressInputs.sectionIndexOf(spine, "OEBPS/nav.xhtml"))
    }

    // ---- pageList ----

    @Test
    fun `page list entries map to reading order indexes and fragments`() {
        val list = ReaderProgressInputs.pageList(
            spine,
            hrefs = listOf("OEBPS/text/ch1.xhtml#p1", "OEBPS/text/ch2.xhtml#p2", "OEBPS/text/ch2.xhtml#p3"),
            labels = listOf("1", "2", "3"),
        )

        assertEquals(listOf(0 to "p1", 1 to "p2", 1 to "p3"), list.entries)
        assertEquals(listOf("1", "2", "3"), list.labels)
    }

    @Test
    fun `page list hrefs match the reading order by path suffix, like the web`() {
        val list = ReaderProgressInputs.pageList(spine, hrefs = listOf("text/ch3.xhtml#p9"), labels = listOf("9"))

        assertEquals(listOf(2 to "p9"), list.entries)
    }

    @Test
    fun `entries outside the reading order are dropped with their labels`() {
        val list = ReaderProgressInputs.pageList(
            spine,
            hrefs = listOf("OEBPS/notes.xhtml#n1", "OEBPS/text/ch1.xhtml#p1"),
            labels = listOf("x", "1"),
        )

        assertEquals(listOf(0 to "p1"), list.entries)
        assertEquals(listOf("1"), list.labels)
    }

    @Test
    fun `an entry without a fragment keeps an empty one`() {
        val list = ReaderProgressInputs.pageList(spine, hrefs = listOf("OEBPS/text/ch2.xhtml"), labels = listOf("4"))

        assertEquals(listOf(1 to ""), list.entries)
    }

    @Test
    fun `the probe gets only this section's non-empty fragments`() {
        val list = ReaderProgressInputs.PageList(
            entries = listOf(0 to "p1", 1 to "p2", 1 to "", 1 to "p3", 2 to "p4"),
            labels = listOf("1", "2", "3", "4", "5"),
        )

        assertEquals(listOf("p2", "p3"), list.fragmentsIn(1))
        assertTrue(list.fragmentsIn(7).isEmpty())
    }

    // ---- spineSignature ----

    @Test
    fun `the spine signature changes with the hrefs and with the file size`() {
        val base = ReaderProgressInputs.spineSignature(spine, byteLength = 1000)

        assertEquals(base, ReaderProgressInputs.spineSignature(spine.toList(), byteLength = 1000))
        assertNotEquals(base, ReaderProgressInputs.spineSignature(spine.reversed(), byteLength = 1000))
        assertNotEquals(base, ReaderProgressInputs.spineSignature(spine, byteLength = 1001))
    }

    // ---- layoutStyle ----

    @Test
    fun `colour-only settings are left out of the layout style`() {
        val day = "--USER__fontSize: 120%; --USER__backgroundColor: #FFFFFF; --USER__textColor: #121212; --USER__appearance: readium-default-on"
        val night = "--USER__fontSize: 120%; --USER__backgroundColor: #000000; --USER__textColor: #FEFEFE; --USER__appearance: readium-night-on"

        assertEquals(ReaderProgressInputs.layoutStyle(day), ReaderProgressInputs.layoutStyle(night))
        assertEquals("--USER__fontSize: 120%", ReaderProgressInputs.layoutStyle(day))
    }

    @Test
    fun `layout settings stay in the layout style`() {
        val small = "--USER__fontSize: 100%; --USER__lineHeight: 1.2"
        val large = "--USER__fontSize: 140%; --USER__lineHeight: 1.2"

        assertNotEquals(ReaderProgressInputs.layoutStyle(small), ReaderProgressInputs.layoutStyle(large))
    }

    // ---- rawHead ----

    @Test
    fun `the raw head is cut after the head and the document closed`() {
        val html = "<?xml version=\"1.0\"?><html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>T</title></HEAD><body><p>long body</p></body></html>"

        assertEquals(
            "<?xml version=\"1.0\"?><html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>T</title></HEAD></html>",
            ReaderProgressInputs.rawHead(html),
        )
    }

    @Test
    fun `a resource without a closing head is passed whole`() {
        val html = "<html><body><p>x</p></body></html>"

        assertEquals(html, ReaderProgressInputs.rawHead(html))
    }
}
