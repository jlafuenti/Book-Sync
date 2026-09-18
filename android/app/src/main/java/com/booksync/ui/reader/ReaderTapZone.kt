package com.booksync.ui.reader

/**
 * Pure tap-zone decision for the reader page (issue #585): whether a tap near
 * the left/right edge of the page should turn to the previous/next page, or
 * fall through to the existing "tap the middle to toggle the toolbar"
 * behavior — which a tap anywhere at all falls through to when the edge-tap
 * setting is off.
 *
 * Top-level and Android-free, like this package's other pure helpers
 * ([spineIndexForHref] and friends in `ReaderPosition.kt`), so it is covered
 * by JVM tests with no Robolectric needed. [ReaderActivity] is the only
 * caller: it turns a Readium `TapEvent`'s pixel `point.x` into [xFraction]
 * (dividing by the navigator's `publicationView.width` — the same
 * denominator Readium's own
 * `org.readium.r2.navigator.util.DirectionalNavigationAdapter` uses) and
 * reads the live reading progression off `navigator.overflow.value` for
 * [isRtl], so right-to-left books get the swapped sides for free rather than
 * from a guess based on static book metadata.
 *
 * `DirectionalNavigationAdapter` was evaluated first and is deliberately not
 * used directly: its edge-zone math is the same range check this function
 * does, but the adapter itself isn't unit-testable outside Robolectric (it
 * reads `View.width` and `TapEvent.point: PointF` internally), and it has no
 * hook to gate on a live text selection or a runtime settings toggle without
 * adding/removing the whole listener on every change — see
 * `ReaderActivity.handleReaderTap`. The actual page turn still goes through
 * Readium's own `OverflowableNavigator.goForward`/`goBackward` — the exact
 * primitives `DirectionalNavigationAdapter` itself calls — so a tap at a
 * chapter boundary crosses into the next/previous chapter exactly like a
 * swipe does; nothing about pagination or chapter boundaries is reimplemented
 * here.
 */
internal enum class ReaderTapAction { TurnPageBackward, TurnPageForward, ToggleBars }

/**
 * Width of each edge zone, as a fraction of the page width — issue #585's
 * "~20-25% each side". 0.22 leaves a 56%-wide middle zone, comfortably large
 * for the existing "tap the middle to toggle the toolbar" behavior and the
 * tour's "tap the middle of the page" anchor (`reader_tap_page` in
 * `TourScript.kt`) to keep working without the user having to aim carefully.
 */
internal const val READER_TAP_EDGE_FRACTION = 0.22

/**
 * @param xFraction Tap x position divided by the page width: 0.0 is the left
 *   edge, 1.0 is the right edge.
 * @param isRtl The book's live effective reading progression is
 *   right-to-left, so left/right swap which page they turn to.
 * @param edgeTapEnabled The reader's "turn pages by tapping the edges"
 *   setting ([ReaderEdgeTapSettings]). `false` makes every tap fall through
 *   to [ReaderTapAction.ToggleBars], matching the reader's behavior before
 *   issue #585.
 * @param edgeFraction Width of each edge zone; defaults to
 *   [READER_TAP_EDGE_FRACTION].
 */
internal fun decideReaderTapAction(
    xFraction: Double,
    isRtl: Boolean,
    edgeTapEnabled: Boolean,
    edgeFraction: Double = READER_TAP_EDGE_FRACTION,
): ReaderTapAction {
    if (!edgeTapEnabled) return ReaderTapAction.ToggleBars
    return when {
        xFraction < edgeFraction ->
            if (isRtl) ReaderTapAction.TurnPageForward else ReaderTapAction.TurnPageBackward
        xFraction > 1.0 - edgeFraction ->
            if (isRtl) ReaderTapAction.TurnPageBackward else ReaderTapAction.TurnPageForward
        else -> ReaderTapAction.ToggleBars
    }
}
