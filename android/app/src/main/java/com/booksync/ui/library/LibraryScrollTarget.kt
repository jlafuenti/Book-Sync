package com.booksync.ui.library

/**
 * Where the tour's picked pair sits in the grid `LibraryScreen.ItemGrid` lays
 * out (issue #597 Track B, `TourNav.OpenLibraryAt`). `ItemGrid` renders `items`
 * — the flat, ungrouped list — directly, one [LibraryItem] per grid cell with
 * no series headers mixed in, so the index into that same list is exactly what
 * `LazyGridState.animateScrollToItem` needs. Pure so it can be unit tested;
 * `LibraryScreen` itself is a `@Composable`, which Kover does not cover.
 *
 * A [LibraryItem] that carries only an ebook or audiobook (no pair) can never
 * match: only `LibraryItem.pair?.id` is compared, deliberately, since a
 * standalone item's own id lives in an independent sequence from pair ids and
 * could otherwise collide by coincidence.
 *
 * @return the index of the item whose `pair.id == pairId`, or null when no
 *   such item is present in [items] — e.g. the grid is still loading, or a
 *   filter the caller forgot to reset is hiding it.
 */
fun libraryIndexOf(items: List<LibraryItem>, pairId: Int): Int? {
    val index = items.indexOfFirst { it.pair?.id == pairId }
    return index.takeIf { it >= 0 }
}

/**
 * The grid cell the walkthrough should spotlight for its "open a book" step.
 *
 * The picker chose [preferredPairId] from the database, which says nothing
 * about where that pair sits in the user's current ordering — it was the
 * *last* card of a 300-item grid in practice. So prefer it only if it is
 * near the top; otherwise take the first synced pair in the grid's own order,
 * and let the tour adopt that pair for the steps that follow. Returns the
 * index to scroll to, or null when the grid holds no synced pair at all.
 */
fun libraryTourTarget(items: List<LibraryItem>, preferredPairId: Int?, nearTop: Int = 6): Int? {
    val preferred = preferredPairId?.let { libraryIndexOf(items, it) }
    if (preferred != null && preferred < nearTop) return preferred
    val firstSynced = items.indexOfFirst { it.pair?.status == "synced" }
    if (firstSynced >= 0) return firstSynced
    return preferred
}
