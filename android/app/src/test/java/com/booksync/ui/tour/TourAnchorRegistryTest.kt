package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The registry real elements publish their window bounds to via
 * `Modifier.tourAnchor` (issue #597 Track A). Plain state holder — the
 * modifier itself is Compose glue and isn't covered here.
 */
class TourAnchorRegistryTest {

    @Test
    fun `set publishes a rect for that anchor only`() {
        val registry = TourAnchorRegistry()
        val rect = Rect(1f, 2f, 3f, 4f)

        registry.set(TourAnchor.CardOverflow, rect)

        assertEquals(rect, registry.rects.value[TourAnchor.CardOverflow])
        assertNull(registry.rects.value[TourAnchor.SheetRead])
    }

    @Test
    fun `set overwrites a previous rect for the same anchor`() {
        val registry = TourAnchorRegistry()
        registry.set(TourAnchor.CardOverflow, Rect(0f, 0f, 1f, 1f))
        val updated = Rect(5f, 5f, 6f, 6f)

        registry.set(TourAnchor.CardOverflow, updated)

        assertEquals(updated, registry.rects.value[TourAnchor.CardOverflow])
    }

    @Test
    fun `clear removes only the named anchor`() {
        val registry = TourAnchorRegistry()
        registry.set(TourAnchor.CardOverflow, Rect(0f, 0f, 1f, 1f))
        registry.set(TourAnchor.SheetRead, Rect(0f, 0f, 1f, 1f))

        registry.clear(TourAnchor.CardOverflow)

        assertNull(registry.rects.value[TourAnchor.CardOverflow])
        assertEquals(1, registry.rects.value.size)
    }

    @Test
    fun `clearing an anchor that was never set is a no-op`() {
        val registry = TourAnchorRegistry()
        registry.clear(TourAnchor.CardOverflow)
        assertEquals(0, registry.rects.value.size)
    }

    // ---- settled (issue #642): screens report when they've finished loading ----

    @Test
    fun `setSettled true adds the screen, false removes it`() {
        val registry = TourAnchorRegistry()

        registry.setSettled(TourScreen.Home, true)
        assertTrue(TourScreen.Home in registry.settled.value)

        registry.setSettled(TourScreen.Home, false)
        assertFalse(TourScreen.Home in registry.settled.value)
    }

    @Test
    fun `setSettled only touches the named screen`() {
        val registry = TourAnchorRegistry()

        registry.setSettled(TourScreen.Home, true)
        registry.setSettled(TourScreen.Library, true)
        registry.setSettled(TourScreen.Home, false)

        assertFalse(TourScreen.Home in registry.settled.value)
        assertTrue(TourScreen.Library in registry.settled.value)
    }

    @Test
    fun `unsetting a screen that was never settled is a no-op`() {
        val registry = TourAnchorRegistry()
        registry.setSettled(TourScreen.Home, false)
        assertEquals(0, registry.settled.value.size)
    }

    // ---- loading (issue #652): "not settled yet" is different from "never reported" ----

    @Test
    fun `setSettled false marks the screen as loading`() {
        val registry = TourAnchorRegistry()
        registry.setSettled(TourScreen.Reader, false)
        assertTrue(TourScreen.Reader in registry.loading.value)
        assertFalse(TourScreen.Reader in registry.settled.value)

        registry.setSettled(TourScreen.Reader, true)
        assertFalse(TourScreen.Reader in registry.loading.value)
        assertTrue(TourScreen.Reader in registry.settled.value)
    }

    @Test
    fun `clearScreen forgets the screen entirely`() {
        val registry = TourAnchorRegistry()
        registry.setSettled(TourScreen.Reader, false)
        registry.setSettled(TourScreen.Home, true)

        registry.clearScreen(TourScreen.Reader)
        registry.clearScreen(TourScreen.Home)

        assertTrue(registry.loading.value.isEmpty())
        assertTrue(registry.settled.value.isEmpty())
    }
}
