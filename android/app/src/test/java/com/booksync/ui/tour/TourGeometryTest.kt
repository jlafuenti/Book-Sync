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
    fun `no room on either side centers the card`() {
        // A hole spanning nearly the whole screen leaves no room above or below.
        val hole = Rect(0f, 50f, 1000f, 1950f)
        assertEquals(Placement.Center, cardPlacement(hole, screen, cardHeight = 300f))
    }

    @Test
    fun `exactly enough room below (plus margin) still counts as below`() {
        val cardHeight = 300f
        val margin = 24f
        val hole = Rect(0f, 0f, 1000f, screen.height - cardHeight - margin)
        assertEquals(Placement.Below, cardPlacement(hole, screen, cardHeight))
    }
}
