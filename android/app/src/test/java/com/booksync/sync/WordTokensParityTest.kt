package com.booksync.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-platform parity for the word-token rule behind the read-along word
 * mark (issue #836).
 *
 * The server stores one start time per whitespace token of a sentence's text
 * preview; the reader marks the n-th token on the page. If the two sides
 * counted tokens differently the mark would land on the wrong word, so
 * [WordTokens.split] must agree with every golden case in
 * server/tests/fixtures/sync_parity/word_tokens_cases.json (copied onto the
 * test classpath by the copySyncParityFixtures Gradle task).
 */
class WordTokensParityTest {

    private fun loadCases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/word_tokens_cases.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonArray }
        ?: error("word_tokens_cases.json not on the test classpath - copySyncParityFixtures must run")

    @Test
    fun split_matches_server_golden_vectors() {
        val cases = loadCases()
        assertTrue("expected at least one golden vector", cases.size > 0)
        for (case in cases) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val text = obj["text"]!!.jsonPrimitive.content
            val expected = obj["expected_tokens"]!!.jsonArray.map { it.jsonPrimitive.content }
            assertEquals("case '$name'", expected, WordTokens.split(text))
        }
    }

    @Test
    fun tokenRanges_cut_the_original_string_into_the_same_tokens() {
        for (case in loadCases()) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val text = obj["text"]!!.jsonPrimitive.content
            val ranges = WordTokens.tokenRanges(text)
            assertEquals("case '$name'", WordTokens.split(text), ranges.map { text.substring(it.first, it.last + 1) })
        }
    }

    @Test
    fun the_whitespace_set_is_the_unicode_White_Space_property() {
        // Java's plain \s is ASCII-only; (?U) widens it to \p{IsWhite_Space}, which
        // is the set Python's str.split() uses on the server.
        val unicodeSpace = Regex("(?U)\\s")
        for (code in 0..0xFFFF) {
            val ch = code.toChar()
            if (ch in '\uD800'..'\uDFFF') continue
            assertEquals(
                "U+%04X".format(code),
                unicodeSpace.matches(ch.toString()),
                WordTokens.isSpace(ch),
            )
        }
    }

    @Test
    fun charRangeOfToken_gives_start_and_end_offsets_in_the_original() {
        val text = "  alpha beta\t\tgamma "
        assertEquals(2..6, WordTokens.charRangeOfToken(text, 0))
        assertEquals(8..11, WordTokens.charRangeOfToken(text, 1))
        assertEquals(14..18, WordTokens.charRangeOfToken(text, 2))
    }

    @Test
    fun charRangeOfToken_is_empty_outside_the_token_list() {
        assertTrue(WordTokens.charRangeOfToken("one two", 2).isEmpty())
        assertTrue(WordTokens.charRangeOfToken("one two", -1).isEmpty())
        assertTrue(WordTokens.charRangeOfToken("", 0).isEmpty())
    }

    @Test
    fun collapse_matches_the_page_script_collapse() {
        // The page script's collapse is s.replace(/\s+/g, ' ').trim(). JS \s also
        // matches U+FEFF and does not match U+0085, unlike White_Space; the
        // token ranges must be computed on the string the page will see.
        assertEquals("a b c", WordTokens.collapse("  a \n\t b  c  "))
        assertEquals("a b", WordTokens.collapse("a﻿b"))
        assertEquals("a\u0085b", WordTokens.collapse("a\u0085b"))
        assertEquals("", WordTokens.collapse(" \n "))
    }

    @Test
    fun parseStarts_reads_the_comma_joined_column() {
        assertEquals(listOf(100, 450, 900), WordTokens.parseStarts("100,450,900").toList())
        assertEquals(listOf(7), WordTokens.parseStarts("7").toList())
        assertTrue(WordTokens.parseStarts("").isEmpty())
    }

    @Test
    fun joinStarts_round_trips() {
        assertEquals("100,450,900", WordTokens.joinStarts(listOf(100, 450, 900)))
        assertEquals(listOf(100, 450, 900), WordTokens.parseStarts(WordTokens.joinStarts(listOf(100, 450, 900))).toList())
    }
}
