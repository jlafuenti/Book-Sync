package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class ReadAlongSettingsTest {
    @Test
    fun `defaults to highlight`() {
        assertEquals(ReadAlongStyle.HIGHLIGHT, ReadAlongSettings(FakeSharedPreferences()).style)
    }

    @Test
    fun `persists under the reader_display key and reads back`() {
        val prefs = FakeSharedPreferences()
        ReadAlongSettings(prefs).style = ReadAlongStyle.UNDERLINE
        assertEquals("underline", prefs.getString("read_along_style", null))
        assertEquals(ReadAlongStyle.UNDERLINE, ReadAlongSettings(prefs).style)
    }

    @Test
    fun `an unknown stored value falls back to highlight`() {
        val prefs = FakeSharedPreferences().apply { edit().putString("read_along_style", "sparkles").apply() }
        assertEquals(ReadAlongStyle.HIGHLIGHT, ReadAlongSettings(prefs).style)
    }

    @Test
    fun `the underline tint is opaque and the highlight tint is translucent`() {
        val s = ReadAlongSettings(FakeSharedPreferences())
        val highlight = s.tint
        s.style = ReadAlongStyle.UNDERLINE
        val underline = s.tint
        assertEquals(0xFF, underline ushr 24)
        assertNotEquals(0xFF, highlight ushr 24)
    }

    @Test
    fun `a change notifies the listener once`() {
        val s = ReadAlongSettings(FakeSharedPreferences())
        val seen = mutableListOf<ReadAlongStyle>()
        s.setListener { seen += it }
        s.style = ReadAlongStyle.UNDERLINE
        assertEquals(listOf(ReadAlongStyle.UNDERLINE), seen)
    }
}
