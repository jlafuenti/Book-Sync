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
