package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
}
