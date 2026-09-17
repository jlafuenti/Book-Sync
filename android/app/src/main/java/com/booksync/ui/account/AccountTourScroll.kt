package com.booksync.ui.account

import com.booksync.ui.tour.TourAnchor

/**
 * Which item of the Account `LazyColumn` a walkthrough step needs on screen
 * (issue #597). The list's items are unconditional and fixed in order —
 * user card, then title/content pairs for Appearance, Storage, Device,
 * Diagnostics, About, Server, Help — so the index of each section title is a
 * constant. Scrolling to the *title* keeps the section's first row visible
 * below it. Returns null for an anchor that is not on this screen.
 */
fun accountSectionIndex(anchor: TourAnchor): Int? = when (anchor) {
    TourAnchor.AccountStorage -> 3
    TourAnchor.AccountServer -> 11
    TourAnchor.AccountReplayTour -> 13
    else -> null
}
