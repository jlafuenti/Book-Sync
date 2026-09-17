package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import org.junit.Assert.assertEquals
import org.junit.Test

/** Pure placement math for [TourOverlay]'s card — issue #597 Track A, plan §4/§6. */
class TourGeometryTest {

    private val screen = Size(1000f, 2000f)

    @Test
    fun `no hole centers the card`() {
        assertEquals(Placement.Center, cardPlacement(hole = null, screen = screen, cardHeight = 300f))
    }

    @Test
    fun `room below the hole places the card below`() {
        val hole = Rect(100f, 100f, 900f, 300f) // bottom = 300, plenty of room below
        assertEquals(Placement.Below, cardPlacement(hole, screen, cardHeight = 300f))
    }

    @Test
    fun `no room below but room above places the card above`() {
        val hole = Rect(100f, 1700f, 900f, 1900f) // bottom = 1900, only 100px left below
        assertEquals(Placement.Above, cardPlacement(hole, screen, cardHeight = 300f))
    }

    @Test
    fun `no room on either side pushes the card to the roomier side, never over the hole`() {
        // A short host (a bottom sheet's content) with the hole near its top:
        // below has more room than above, so the card goes below and is
        // clamped to the host's bottom edge rather than centred over the hole.
        val sheet = Size(1000f, 900f)
        val hole = Rect(0f, 200f, 1000f, 320f)
        val placement = cardPlacement(hole, sheet, cardHeight = 700f)
        assertEquals(Placement.Below, placement)
        assertEquals(200f, cardOffsetY(placement, hole, sheet, cardHeight = 700f))

        // Hole near the bottom: above has more room; clamped to the top edge.
        val lowHole = Rect(0f, 700f, 1000f, 850f)
        val above = cardPlacement(lowHole, sheet, cardHeight = 700f)
        assertEquals(Placement.Above, above)
        assertEquals(0f, cardOffsetY(above, lowHole, sheet, cardHeight = 700f))
    }

    @Test
    fun `offsets sit a gap away from the hole when there is room`() {
        val hole = Rect(100f, 100f, 900f, 300f)
        assertEquals(316f, cardOffsetY(Placement.Below, hole, screen, cardHeight = 300f))
        val lowHole = Rect(100f, 1700f, 900f, 1900f)
        assertEquals(1384f, cardOffsetY(Placement.Above, lowHole, screen, cardHeight = 300f))
        assertEquals(null, cardOffsetY(Placement.Center, null, screen, cardHeight = 300f))
    }

    @Test
    fun `exactly enough room below (plus margin) still counts as below`() {
        val cardHeight = 300f
        val margin = 24f
        val hole = Rect(0f, 0f, 1000f, screen.height - cardHeight - margin)
        assertEquals(Placement.Below, cardPlacement(hole, screen, cardHeight))
    }
}
