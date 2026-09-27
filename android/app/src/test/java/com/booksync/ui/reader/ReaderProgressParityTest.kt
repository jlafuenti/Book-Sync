package com.booksync.ui.reader

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Issue #730. One rule with web/src/lib/readerProgress.js: this suite and the web's
 * `readerProgress.test.js` load the *same* golden vectors
 * (`server/tests/fixtures/sync_parity/reader_progress_cases.json`, copied onto the test
 * classpath by the build), so the reader progress indicator cannot disagree between
 * platforms.
 */
class ReaderProgressParityTest {

    private fun cases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/reader_progress_cases.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonObject }
        ?: error("reader_progress_cases.json is not on the test classpath")

    @Test
    fun `cycles modes`() {
        cases()["next_mode"]!!.jsonArray.forEach { pair ->
            val (from, to) = pair.jsonArray
            val fromStr = from.takeIf { it != JsonNull }?.jsonPrimitive?.contentOrNull
            assertEquals(to.jsonPrimitive.content, ReaderProgress.nextProgressMode(fromStr))
        }
    }

    @Test
    fun `parses progress mode`() {
        cases()["parse_progress_mode"]!!.jsonArray.forEach { pair ->
            val (v, want) = pair.jsonArray
            val vStr = v.takeIf { it != JsonNull }?.jsonPrimitive?.contentOrNull
            assertEquals(want.jsonPrimitive.content, ReaderProgress.parseProgressMode(vStr))
        }
    }

    @Test
    fun `parses page mode`() {
        cases()["parse_page_mode"]!!.jsonArray.forEach { pair ->
            val (v, want) = pair.jsonArray
            val vStr = v.takeIf { it != JsonNull }?.jsonPrimitive?.contentOrNull
            assertEquals(want.jsonPrimitive.content, ReaderProgress.parsePageMode(vStr))
        }
    }

    @Test
    fun `page in section`() {
        cases()["page_in_section"]!!.jsonArray.forEach { element ->
            val c = element.jsonObject
            val progression = c["progression"]!!.jsonPrimitive.double
            val total = c["total"]!!.jsonPrimitive.int
            val expect = c["expect"]?.takeIf { it != JsonNull }?.jsonPrimitive?.int
            assertEquals(expect, ReaderProgress.pageInSection(progression, total))
        }
    }

    @Test
    fun `ebook position`() {
        cases()["ebook_position"]!!.jsonArray.forEach { element ->
            val c = element.jsonObject
            val counts = c["counts"]!!.jsonArray.map { it.jsonPrimitive.int }
            val section = c["section"]!!.jsonPrimitive.int
            val page = c["page"]!!.jsonPrimitive.int
            val expectObj = c["expect"]
            val result = ReaderProgress.ebookPosition(counts, section, page)
            if (expectObj == null || expectObj == JsonNull) {
                assertNull(result)
            } else {
                val e = expectObj.jsonObject
                assertEquals(e["page"]!!.jsonPrimitive.int, result!!.page)
                assertEquals(e["total"]!!.jsonPrimitive.int, result.total)
            }
        }
    }

    @Test
    fun `resolves page labels`() {
        cases()["resolve_page_label"]!!.jsonArray.forEach { element ->
            val c = element.jsonObject
            val name = c["name"]!!.jsonPrimitive.content
            val input = c["in"]!!.jsonObject
            val pageMode = input["pageMode"]!!.jsonPrimitive.content
            val fraction = input["fraction"]?.takeIf { it != JsonNull }?.jsonPrimitive?.doubleOrNull
            val ebookObj = input["ebook"]?.takeIf { it != JsonNull }?.jsonObject
            val ebook = ebookObj?.let {
                ReaderProgress.EbookPosition(it["page"]!!.jsonPrimitive.int, it["total"]!!.jsonPrimitive.int)
            }
            val printListObj = input["printList"]?.takeIf { it != JsonNull }?.jsonObject
            val printList = printListObj?.let {
                ReaderProgress.PrintList(
                    currentLabel = it["currentLabel"]?.takeIf { v -> v != JsonNull }?.jsonPrimitive?.contentOrNull,
                    firstLabel = it["firstLabel"]?.takeIf { v -> v != JsonNull }?.jsonPrimitive?.contentOrNull,
                    lastLabel = it["lastLabel"]?.takeIf { v -> v != JsonNull }?.jsonPrimitive?.contentOrNull,
                )
            }
            val printPageCount = input["printPageCount"]?.takeIf { it != JsonNull }?.jsonPrimitive?.intOrNull

            val result = ReaderProgress.resolvePageLabel(pageMode, fraction, ebook, printList, printPageCount)

            val expect = c["expect"]!!.jsonObject
            assertEquals(name, expect["current"]?.takeIf { it != JsonNull }?.jsonPrimitive?.contentOrNull, result.current)
            assertEquals(name, expect["total"]?.takeIf { it != JsonNull }?.jsonPrimitive?.contentOrNull, result.total)
            assertEquals(name, expect["kind"]!!.jsonPrimitive.content, result.kind)
            assertEquals(name, c["text"]!!.jsonPrimitive.content, ReaderProgress.formatPageLabel(result))
        }
    }

    @Test
    fun `chapter text`() {
        cases()["chapter_text"]!!.jsonArray.forEach { element ->
            val (p, t, want) = element.jsonArray
            val page = p.takeIf { it != JsonNull }?.jsonPrimitive?.intOrNull
            val total = t.takeIf { it != JsonNull }?.jsonPrimitive?.intOrNull
            assertEquals(want.jsonPrimitive.content, ReaderProgress.formatChapterPage(page, total))
        }
    }

    @Test
    fun `reading speed`() {
        cases()["speed"]!!.jsonArray.forEach { element ->
            val c = element.jsonObject
            val name = c["name"]!!.jsonPrimitive.content
            val samples = c["samples"]!!.jsonArray.map { it.jsonPrimitive.double }
            val expect = c["expect"]!!.jsonPrimitive.double
            assertEquals(name, expect, ReaderProgress.charsPerSecond(samples), 1e-9)
        }
    }

    @Test
    fun `adds samples`() {
        cases()["add_sample"]!!.jsonArray.forEach { element ->
            val c = element.jsonObject
            val samples = c["samples"]!!.jsonArray.map { it.jsonPrimitive.double }
            val chars = c["chars"]!!.jsonPrimitive.double
            val dwell = c["dwell"]!!.jsonPrimitive.double
            val expect = c["expect"]!!.jsonArray.map { it.jsonPrimitive.double }
            assertEquals(expect, ReaderProgress.addSpeedSample(samples, chars, dwell))
        }
    }

    @Test
    fun `seconds left`() {
        cases()["seconds_left"]!!.jsonArray.forEach { element ->
            val c = element.jsonObject
            val chars = c["chars"]!!.jsonPrimitive.int
            val page = c["page"]!!.jsonPrimitive.int
            val pages = c["pages"]!!.jsonPrimitive.int
            val cps = c["cps"]!!.jsonPrimitive.double
            val expect = c["expect"]?.takeIf { it != JsonNull }?.jsonPrimitive?.double
            val got = ReaderProgress.secondsLeftInSection(chars, page, pages, cps)
            if (expect == null) assertNull(got) else assertEquals(expect, got!!, 1e-6)
        }
    }

    @Test
    fun `time text`() {
        cases()["time_text"]!!.jsonArray.forEach { element ->
            val (s, want) = element.jsonArray
            val seconds = s.takeIf { it != JsonNull }?.jsonPrimitive?.doubleOrNull
            assertEquals(want.jsonPrimitive.content, ReaderProgress.formatTimeLeft(seconds))
        }
    }

    @Test
    fun `keeps at most 50 samples`() {
        var s = emptyList<Double>()
        repeat(60) { s = ReaderProgress.addSpeedSample(s, 1500.0, 60.0) }
        assertEquals(50, s.size)
    }

    // JSON cannot carry NaN, so these are plain assertions rather than fixture cases —
    // pinned on both platforms (readerProgress.test.js has the JS equivalents).
    @Test
    fun `rejects a NaN sample or dwell time`() {
        assertEquals(emptyList<Double>(), ReaderProgress.addSpeedSample(emptyList(), Double.NaN, 60.0))
        assertEquals(emptyList<Double>(), ReaderProgress.addSpeedSample(emptyList(), 1500.0, Double.NaN))
    }

    @Test
    fun `rejects a NaN reading speed`() {
        assertNull(ReaderProgress.secondsLeftInSection(1000, 1, 2, Double.NaN))
    }

    @Test
    fun `treats a NaN progression as the start of the section`() {
        assertEquals(1, ReaderProgress.pageInSection(Double.NaN, 10))
    }

    @Test
    fun `exercises every section of the fixture`() {
        val covered = setOf(
            "next_mode", "parse_progress_mode", "parse_page_mode", "page_in_section", "ebook_position",
            "resolve_page_label", "chapter_text", "speed", "add_sample", "seconds_left", "time_text",
        )
        val keys = cases().keys.filter { it != "_doc" }.toSet()
        assertEquals(covered, keys)
    }
}
