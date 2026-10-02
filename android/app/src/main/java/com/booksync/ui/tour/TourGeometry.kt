package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.unit.dp

/**
 * Blank room the card sheet adds above its rows while the walkthrough is on a sheet step
 * (issue #788). The sheet steps' overlay can only cover the sheet's own content — the sheet
 * is its own window — and without this the content was too short for the card to fit above
 * or below the row it pointed at, so [cardPlacement] fell back to the roomier side and the
 * card landed on the row. A typical sheet card measures about 225 dp; the sheet's header adds
 * the rest of the margin.
 */
val SHEET_TOUR_CARD_ROOM = 240.dp

/** Where [TourOverlay]'s card goes relative to the spotlight hole. */
enum class Placement { Above, Below, Center }

/**
 * Pure placement math for the walkthrough card (issue #597).
 *
 * Below the hole when the card fits there, else above when it fits there.
 * When it fits on neither side — a short host such as a bottom sheet's
 * content, or a hole spanning most of the screen — the card is still pushed
 * to whichever side has more room rather than centred, so it covers as
 * little of the highlighted control as the space allows; [cardOffsetY]
 * clamps that position to the host's bounds. Only a missing hole centres it.
 *
 * [preferAbove] tries above first (issue #788): on the card sheet the rows under the
 * spotlit one are what the card goes on to explain, so they should stay visible.
 */
fun cardPlacement(hole: Rect?, screen: Size, cardHeight: Float, preferAbove: Boolean = false): Placement {
    if (hole == null) return Placement.Center
    val margin = 24f
    val spaceBelow = screen.height - hole.bottom
    val spaceAbove = hole.top
    return when {
        preferAbove && spaceAbove >= cardHeight + margin -> Placement.Above
        spaceBelow >= cardHeight + margin -> Placement.Below
        spaceAbove >= cardHeight + margin -> Placement.Above
        spaceBelow >= spaceAbove -> Placement.Below
        else -> Placement.Above
    }
}

/** An outline drawn around the spotlight hole, in the overlay's own pixels. */
data class SpotlightRing(val rect: Rect, val cornerRadiusPx: Float, val strokePx: Float)

/**
 * The outline that keeps the spotlight visible on any background (issue #764): on a dark
 * reader theme the control behind a cleared hole is as dark as the scrim, so the hole alone
 * shows nothing. The ring sits just outside the hole's edge, so it never covers the control.
 * Null when there is no hole, and when the "hole" is the whole page ([blockNothing], the
 * selection steps), where an outline around the page would be noise.
 */
fun spotlightRing(hole: Rect?, blockNothing: Boolean, strokePx: Float, holeCornerPx: Float): SpotlightRing? {
    if (hole == null || blockNothing) return null
    val half = strokePx / 2f
    return SpotlightRing(
        rect = Rect(hole.left - half, hole.top - half, hole.right + half, hole.bottom + half),
        cornerRadiusPx = holeCornerPx + half,
        strokePx = strokePx,
    )
}

/**
 * The card's top edge for [placement], in the overlay's own pixels, clamped so
 * the card never leaves the host: a "below" card that would run off the
 * bottom sits on the bottom edge, an "above" card never rises past the top.
 * Null for [Placement.Center] (the host centres it).
 */
fun cardOffsetY(placement: Placement, hole: Rect?, screen: Size, cardHeight: Float): Float? {
    if (placement == Placement.Center || hole == null) return null
    val gap = 16f
    val raw = when (placement) {
        Placement.Below -> hole.bottom + gap
        Placement.Above -> hole.top - cardHeight - gap
        Placement.Center -> return null
    }
    val maxTop = (screen.height - cardHeight).coerceAtLeast(0f)
    return raw.coerceIn(0f, maxTop)
}
