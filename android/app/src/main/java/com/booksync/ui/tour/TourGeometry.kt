package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size

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
 */
fun cardPlacement(hole: Rect?, screen: Size, cardHeight: Float): Placement {
    if (hole == null) return Placement.Center
    val margin = 24f
    val spaceBelow = screen.height - hole.bottom
    val spaceAbove = hole.top
    return when {
        spaceBelow >= cardHeight + margin -> Placement.Below
        spaceAbove >= cardHeight + margin -> Placement.Above
        spaceBelow >= spaceAbove -> Placement.Below
        else -> Placement.Above
    }
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
