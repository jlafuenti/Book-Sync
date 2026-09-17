package com.booksync.ui.tour

import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Where every tagged control on screen currently is, in window coordinates
 * (issue #597 Track A). `Modifier.tourAnchor` is the writer; [TourController]
 * reads [rects] to find the hole for the step currently running, and to
 * detect a step whose anchor hasn't shown up yet (see its anchor-timeout
 * handling).
 *
 * One instance for the whole app — the reader is a second Activity, and its
 * two View-based anchors (Track C) publish into the same registry.
 */
@Singleton
class TourAnchorRegistry @Inject constructor() {
    private val _rects = MutableStateFlow<Map<TourAnchor, Rect>>(emptyMap())
    val rects: StateFlow<Map<TourAnchor, Rect>> = _rects.asStateFlow()

    fun set(anchor: TourAnchor, rect: Rect) {
        _rects.value = _rects.value + (anchor to rect)
    }

    fun clear(anchor: TourAnchor) {
        if (!_rects.value.containsKey(anchor)) return
        _rects.value = _rects.value - anchor
    }
}

/**
 * Provided once, near the outer `NavHost` in `BookSyncNavigation`, so every
 * screen below it can tag a control with `Modifier.tourAnchor` without
 * threading the registry through every composable's parameter list.
 */
val LocalTourRegistry = staticCompositionLocalOf<TourAnchorRegistry> {
    error(
        "LocalTourRegistry has no value — wrap the content in " +
            "CompositionLocalProvider(LocalTourRegistry provides registry)",
    )
}

/**
 * Publishes this element's window bounds to [LocalTourRegistry] under
 * [anchor] on every layout pass, and clears them when the element leaves
 * composition — so a control that Track B/C never got to tag simply never
 * appears in [TourAnchorRegistry.rects], and [TourController] renders that
 * step degraded instead of pointing at nothing.
 */
fun Modifier.tourAnchor(anchor: TourAnchor): Modifier = composed {
    val registry = LocalTourRegistry.current
    DisposableEffect(anchor, registry) {
        onDispose { registry.clear(anchor) }
    }
    onGloballyPositioned { coordinates ->
        registry.set(anchor, coordinates.boundsInWindow())
    }
}
