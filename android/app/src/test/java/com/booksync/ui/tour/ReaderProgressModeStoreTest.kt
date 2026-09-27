package com.booksync.ui.tour

import com.booksync.ui.reader.FakeSharedPreferences
import com.booksync.ui.reader.ReaderProgressPrefs
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The seam [TourController] uses to record and restore the reader's
 * tap-to-cycle progress mode around a walkthrough run (issue #743) — see
 * `reader_progress` in `TourScript.kt`. Backed by the same `reader_display`
 * `SharedPreferences` file [ReaderProgressPrefs] already persists the mode
 * in, so a value this store writes is exactly what the reader reads back,
 * and vice versa. No Robolectric in this module (see
 * `ReaderProgressPrefsTest`), so this runs against [FakeSharedPreferences].
 */
class ReaderProgressModeStoreTest {

    @Test
    fun `get reads the default mode from an empty store`() {
        val store = ReaderProgressModeStore(FakeSharedPreferences())

        assertEquals("percent", store.get())
    }

    @Test
    fun `set writes the mode back through the same backing store`() {
        val backing = FakeSharedPreferences()
        val store = ReaderProgressModeStore(backing)

        store.set("chapter")

        assertEquals("chapter", store.get())
        assertEquals("chapter", ReaderProgressPrefs(backing).progressMode)
    }

    @Test
    fun `a mode already written by ReaderProgressPrefs is visible to get`() {
        val backing = FakeSharedPreferences()
        ReaderProgressPrefs(backing).progressMode = "time"

        assertEquals("time", ReaderProgressModeStore(backing).get())
    }
}
