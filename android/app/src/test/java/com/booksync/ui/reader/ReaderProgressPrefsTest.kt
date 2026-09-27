package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #730. Per-device reader progress preferences, backed by the same
 * `reader_display` file [ReaderDisplaySettings] and [ReaderEdgeTapSettings]
 * already use. No Robolectric in this module, so these run against
 * [FakeSharedPreferences] rather than a real Android `SharedPreferences`.
 */
class ReaderProgressPrefsTest {

    @Test
    fun `defaults are percent, ebook, no samples and no print page count`() {
        val prefs = ReaderProgressPrefs(FakeSharedPreferences())

        assertEquals("percent", prefs.progressMode)
        assertEquals("ebook", prefs.pageMode)
        assertTrue(prefs.speedSamples.isEmpty())
        assertNull(prefs.printPageCount(42))
    }

    @Test
    fun `progress mode round trips through the backing store`() {
        val backing = FakeSharedPreferences()
        ReaderProgressPrefs(backing).progressMode = "chapter"

        assertEquals("chapter", ReaderProgressPrefs(backing).progressMode)
    }

    @Test
    fun `an unrecognized stored progress mode falls back to the default`() {
        val backing = FakeSharedPreferences()
        backing.edit().putString("progress_mode", "not-a-mode").apply()

        assertEquals("percent", ReaderProgressPrefs(backing).progressMode)
    }

    @Test
    fun `page mode round trips through the backing store`() {
        val backing = FakeSharedPreferences()
        ReaderProgressPrefs(backing).pageMode = "print"

        assertEquals("print", ReaderProgressPrefs(backing).pageMode)
    }

    @Test
    fun `speed samples round trip as a JSON array of doubles`() {
        val backing = FakeSharedPreferences()
        ReaderProgressPrefs(backing).speedSamples = listOf(12.5, 30.0, 7.25)

        assertEquals(listOf(12.5, 30.0, 7.25), ReaderProgressPrefs(backing).speedSamples)
    }

    @Test
    fun `a corrupt speed_samples string reads as empty`() {
        val backing = FakeSharedPreferences()
        backing.edit().putString("speed_samples", "not json at all").apply()

        assertTrue(ReaderProgressPrefs(backing).speedSamples.isEmpty())
    }

    @Test
    fun `print page count round trips per ebook id, independent of other ids`() {
        val backing = FakeSharedPreferences()
        val prefs = ReaderProgressPrefs(backing)
        prefs.setPrintPageCount(7, 342)

        assertEquals(342, ReaderProgressPrefs(backing).printPageCount(7))
        assertNull(ReaderProgressPrefs(backing).printPageCount(8))
    }

    @Test
    fun `setPrintPageCount with null removes the stored key rather than storing 0`() {
        val backing = FakeSharedPreferences()
        val prefs = ReaderProgressPrefs(backing)
        prefs.setPrintPageCount(7, 342)

        prefs.setPrintPageCount(7, null)

        assertNull(prefs.printPageCount(7))
        assertFalse(backing.contains("print_pages_7"))
    }
}
