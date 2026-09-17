package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size

/** Where [TourOverlay] should sit its card relative to the spotlighted hole. */
enum class Placement { Above, Below, Center }

/**
 * Pure placement math for the tour card (issue #597 §4/§6): below the hole
 * when [cardHeight] fits there (with a small margin), else above when it fits
 * there, else centered — used for a degraded card (no hole at all) and for
 * the rare hole that leaves no room on either side.
 */
fun cardPlacement(hole: Rect?, screen: Size, cardHeight: Float): Placement {
    if (hole == null) return Placement.Center
    val margin = 24f
    val spaceBelow = screen.height - hole.bottom
    val spaceAbove = hole.top
    return when {
        spaceBelow >= cardHeight + margin -> Placement.Below
        spaceAbove >= cardHeight + margin -> Placement.Above
        else -> Placement.Center
    }
}
