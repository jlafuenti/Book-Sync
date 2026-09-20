package com.booksync.ui.tour

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
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
 * reads [rects] to find the hole for the step currently running and to
 * resolve whether that step's anchor is [AnchorResolution.Found],
 * [AnchorResolution.Pending] or [AnchorResolution.Missing] (issue #642) —
 * [settled] is the other half of that decision, reported by each screen
 * through [TourScreenSettled].
 *
 * One instance for the whole app — the reader is a second Activity, and its
 * two View-based anchors (Track C) publish into the same registry.
 */
@Singleton
class TourAnchorRegistry @Inject constructor() {
    private val _rects = MutableStateFlow<Map<TourAnchor, Rect>>(emptyMap())
    val rects: StateFlow<Map<TourAnchor, Rect>> = _rects.asStateFlow()

    /** The anchor the current step wants on screen; tagged elements scroll themselves into view for it. */
    private val _wanted = MutableStateFlow<TourAnchor?>(null)
    val wanted: StateFlow<TourAnchor?> = _wanted.asStateFlow()
    fun setWanted(anchor: TourAnchor?) { _wanted.value = anchor }

    /**
     * Screens that have finished loading (data fetched, role known, navigator
     * ready — issue #642). [TourController] only starts counting down to
     * [AnchorResolution.Missing] once the step's own screen is in here; before
     * that an absent anchor just means the screen hasn't drawn it yet.
     */
    private val _settled = MutableStateFlow<Set<TourScreen>>(emptySet())
    val settled: StateFlow<Set<TourScreen>> = _settled.asStateFlow()

    /**
     * Screens that are on screen and have said they are *still loading* (issue #652) — as
     * opposed to a screen nobody has heard from. The difference matters to the safety-net
     * cap: a reader parsing a full-length book took 41 s on the emulator, and capping that
     * told the user a control was missing ten seconds before it appeared. Only a screen
     * that has reported nothing at all is capped; a loading one is waited for.
     */
    private val _loading = MutableStateFlow<Set<TourScreen>>(emptySet())
    val loading: StateFlow<Set<TourScreen>> = _loading.asStateFlow()

    /** `false` means "I am here and still loading", not "forget me" — see [clearScreen]. */
    fun setSettled(screen: TourScreen, settled: Boolean) {
        if (settled) {
            _loading.value = _loading.value - screen
            _settled.value = _settled.value + screen
        } else {
            _settled.value = _settled.value - screen
            _loading.value = _loading.value + screen
        }
    }

    /** The screen is gone (disposed, Activity destroyed): neither settled nor loading. */
    fun clearScreen(screen: TourScreen) {
        _settled.value = _settled.value - screen
        _loading.value = _loading.value - screen
    }

    fun set(anchor: TourAnchor, rect: Rect) {
        // The first layout pass of a not-yet-measured element reports an empty
        // rect; that is not a position anyone can spotlight.
        if (rect.width <= 0f || rect.height <= 0f) return
        if (anchor !in _rects.value) android.util.Log.d("Tour", "anchor $anchor registered at $rect")
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
@OptIn(ExperimentalFoundationApi::class)
fun Modifier.tourAnchor(anchor: TourAnchor): Modifier = composed {
    val registry = LocalTourRegistry.current
    DisposableEffect(anchor, registry) {
        onDispose { registry.clear(anchor) }
    }
    // A tagged element below the fold of a scrolling column brings itself into
    // view when its step is current; lazy lists still need the screen to scroll
    // to the item, since an uncomposed item has no modifier to ask.
    val requester = remember { BringIntoViewRequester() }
    val wanted by registry.wanted.collectAsState()
    LaunchedEffect(wanted, anchor) {
        if (wanted == anchor) requester.bringIntoView()
    }
    bringIntoViewRequester(requester).onGloballyPositioned { coordinates ->
        registry.set(anchor, coordinates.boundsInWindow())
    }
}

/**
 * A screen reports through this whenever its own "has finished loading" flag
 * changes (issue #642) — data fetched, role resolved, a reader navigator
 * ready. [TourController] uses [TourAnchorRegistry.settled] to tell "the
 * control isn't on this screen" from "this screen hasn't drawn it yet",
 * which is what replaces the old fixed anchor timeout. Unset on dispose so a
 * screen that navigates away doesn't leave a stale "settled" behind for the
 * next step to trust.
 */
@Composable
fun TourScreenSettled(screen: TourScreen, settled: Boolean) {
    val registry = LocalTourRegistry.current
    DisposableEffect(screen, settled) {
        registry.setSettled(screen, settled)
        onDispose { registry.clearScreen(screen) }
    }
}
