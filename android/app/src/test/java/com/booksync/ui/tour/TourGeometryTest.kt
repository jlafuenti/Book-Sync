package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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

    // ---- spotlight ring (issue #764) ----

    @Test
    fun `a Found hole gets a ring just outside its edge, with a matching corner radius`() {
        val hole = Rect(100f, 200f, 300f, 260f)
        val ring = spotlightRing(hole, blockNothing = false, strokePx = 6f, holeCornerPx = 12f)
        assertEquals(Rect(97f, 197f, 303f, 263f), ring?.rect)
        assertEquals(15f, ring?.cornerRadiusPx)
        assertEquals(6f, ring?.strokePx)
    }

    @Test
    fun `no hole means no ring`() {
        assertNull(spotlightRing(null, blockNothing = false, strokePx = 6f, holeCornerPx = 12f))
    }

    @Test
    fun `the full-page selection hole gets no ring`() {
        val fullPage = Rect(0f, 0f, 1000f, 2000f)
        assertNull(spotlightRing(fullPage, blockNothing = true, strokePx = 6f, holeCornerPx = 12f))
    }

    @Test
    fun `preferring above places the card above whenever it fits there, even with room below`() {
        // Issue #788: on the sheet, the rows under the spotlit one are what the card talks
        // about next (Read or Listen), so the card takes the room above when it has it.
        val hole = Rect(0f, 1300f, 1000f, 1420f)
        val tall = Size(1000f, 2400f)
        assertEquals(Placement.Below, cardPlacement(hole, tall, cardHeight = 600f))
        assertEquals(Placement.Above, cardPlacement(hole, tall, cardHeight = 600f, preferAbove = true))
        // Without the room above, it still falls back to below.
        val low = Rect(0f, 300f, 1000f, 420f)
        assertEquals(Placement.Below, cardPlacement(low, tall, cardHeight = 600f, preferAbove = true))
    }

    @Test
    fun `the room the sheet adds lets the Read or Listen card sit clear above its row`() {
        // Issue #788, measured off a phone screenshot (1080 px wide, 2.625 px/dp): the sheet's
        // content was 1123 px tall with the Read row 580 px down it, and the card 590 px tall.
        // Neither side fitted, so the card took the top of the sheet and covered the row.
        val density = 2.625f
        val card = 590f
        val row = Rect(0f, 580f, 1080f, 708f)
        val bare = Size(1080f, 1123f)
        val bareOffset = cardOffsetY(cardPlacement(row, bare, card), row, bare, card)!!
        assertEquals("before: the card overlaps the row", true, bareOffset + card > row.top)

        val room = SHEET_TOUR_CARD_ROOM.value * density
        val roomy = Size(bare.width, bare.height + room)
        val shifted = row.translate(0f, room)
        val placement = cardPlacement(shifted, roomy, card, preferAbove = true)
        assertEquals(Placement.Above, placement)
        val offset = cardOffsetY(placement, shifted, roomy, card)!!
        assertEquals("after: the card ends above the row", true, offset + card <= shifted.top)
    }
}
