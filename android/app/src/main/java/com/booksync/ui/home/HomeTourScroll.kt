package com.booksync.ui.home

import com.booksync.ui.tour.TourAnchor

/**
 * Which `LazyColumn` item the walkthrough must scroll Home to before it can
 * spotlight [anchor] (issue #597).
 *
 * Home's sections render only when they have content, and a `LazyColumn`
 * never lays out an item below the fold — so a section the user has not
 * scrolled to has no anchor rect and the tour would wrongly call it empty.
 * [sections] is the list of items Home is about to emit, in order, one entry
 * per `item {}`: the anchor that item carries, or null for an item with none
 * (the version banner). Returns the index to scroll to, or null when the
 * section is genuinely absent.
 */
fun homeSectionIndex(anchor: TourAnchor, sections: List<TourAnchor?>): Int? {
    val index = sections.indexOf(anchor)
    return index.takeIf { it >= 0 }
}

/** The Home anchors, so a screen can tell "this step is about me" from any other step. */
val HOME_TOUR_ANCHORS: Set<TourAnchor> = setOf(
    TourAnchor.HomeContinueReading,
    TourAnchor.HomeRecentlyAdded,
    TourAnchor.HomeInQueue,
)
