package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** Where the tour is right now. */
sealed class TourState {
    data object Idle : TourState()

    data class Running(
        val index: Int,
        val step: TourStep,
        /** The spotlighted control's window bounds, or null while waiting / degraded. */
        val anchor: Rect?,
        /** True once [step] has waited [TourController] out without its anchor showing up. */
        val degraded: Boolean,
        val pairId: Int?,
        val total: Int,
    ) : TourState()

    data object Finished : TourState()
}

/** A navigation request the nav host (and, for the reader-only ones, Track C) must carry out. */
sealed class TourNav {
    data class GoToTab(val tab: String) : TourNav()
    data class OpenLibraryAt(val pairId: Int) : TourNav()
    data class OpenDetails(val pairId: Int) : TourNav()
    data class OpenReader(val pairId: Int) : TourNav()
    data object PopToMain : TourNav()
    data object ShowReaderBars : TourNav()
    data object SkipToToolbarSync : TourNav()
}

/**
 * The walkthrough's state machine (issue #597 §3). Plain Kotlin — no Android
 * types beyond `androidx.compose.ui.geometry.Rect`, which is a pure value
 * class — so it is fully covered by JVM tests; the reader is a second
 * Activity, so this has to be app-scoped rather than living in a
 * MainActivity ViewModel (see `di/AppModule.kt`'s `provideTourController`,
 * which is where the [clock]/[anchorTimeoutMs] defaults actually apply —
 * Dagger's generated factory does not honour Kotlin default arguments, so a
 * plain `@Provides` function calls this constructor instead of an
 * `@Inject constructor`).
 */
class TourController(
    private val registry: TourAnchorRegistry,
    private val prefs: TourPrefs,
    private val picker: TourPairPicker,
    private val scope: CoroutineScope,
    /**
     * Reserved for future use (step timing / diagnostics); the anchor
     * timeout itself is driven by a coroutine `delay` under [scope], not by
     * polling this clock, so tests control it with `advanceTimeBy` /
     * `advanceUntilIdle` rather than a fake clock value.
     */
    private val clock: () -> Long = System::currentTimeMillis,
    private val anchorTimeoutMs: Long = 1500,
) {
    private val _state = MutableStateFlow<TourState>(TourState.Idle)
    val state: StateFlow<TourState> = _state.asStateFlow()

    private val _nav = MutableSharedFlow<TourNav>(extraBufferCapacity = 8)
    val nav: SharedFlow<TourNav> = _nav.asSharedFlow()

    private var steps: List<TourStep> = emptyList()
    private var pairId: Int? = null
    private var anchorWaitJob: Job? = null

    /** Picks a pair (degrading gracefully to the skip card when none qualifies) and enters step 0. */
    fun start() {
        anchorWaitJob?.cancel()
        scope.launch {
            val picked = picker.pick()
            pairId = picked
            steps = buildSteps(picked)
            enter(0, previousScreen = null)
        }
    }

    /** Ignored on a [Advance.TapAnchor] step — the card says to tap the highlighted control instead. */
    fun next() {
        val running = _state.value as? TourState.Running ?: return
        if (running.step.advance is Advance.TapAnchor) return
        advanceFrom(running.index)
    }

    fun back() {
        val running = _state.value as? TourState.Running ?: return
        if (running.index == 0) return
        enter(running.index - 1, previousScreen = running.step.screen)
    }

    /** Quits the tour from wherever it is right now. */
    fun quit() = finish()

    /** A no-op unless the current step is a skippable [Advance.WaitFor]. */
    fun skip() {
        val running = _state.value as? TourState.Running ?: return
        val advance = running.step.advance
        if (advance !is Advance.WaitFor || !advance.skippable) return
        if (advance.event is TourEvent.ReaderSyncedSelection) {
            _nav.tryEmit(TourNav.SkipToToolbarSync)
        }
        advanceFrom(running.index)
    }

    /** Advances a [Advance.TapAnchor] or [Advance.WaitFor] step whose expected event just fired. */
    fun onEvent(event: TourEvent) {
        val running = _state.value as? TourState.Running ?: return
        val matched = when (val advance = running.step.advance) {
            is Advance.TapAnchor -> advance.expect.matchesKind(event)
            is Advance.WaitFor -> advance.event.matchesKind(event)
            Advance.Next -> false
        }
        if (matched) advanceFrom(running.index)
    }

    private fun advanceFrom(currentIndex: Int) {
        val leaving = steps[currentIndex]
        if (currentIndex >= steps.size - 1) {
            finish()
            return
        }
        val nextIndex = currentIndex + 1
        val nextStep = steps[nextIndex]
        if (isReaderOrPlayer(leaving.screen) && !isReaderOrPlayer(nextStep.screen)) {
            _nav.tryEmit(TourNav.PopToMain)
        }
        enter(nextIndex, previousScreen = leaving.screen)
    }

    private fun enter(index: Int, previousScreen: TourScreen?) {
        anchorWaitJob?.cancel()
        val step = steps[index]
        emitEnterNav(step, previousScreen, isFirstOccurrenceOfScreen(step.screen, index))

        val immediateRect = step.anchor?.let { registry.rects.value[it] }
        _state.value = TourState.Running(
            index = index,
            step = step,
            anchor = immediateRect,
            // Not degraded yet even when the anchor is still missing: that's
            // only decided once the wait below times out. A step with no
            // anchor at all (a plain info card) is never degraded.
            degraded = false,
            pairId = pairId,
            total = steps.size,
        )

        if (step.anchor != null && immediateRect == null) {
            val anchor = step.anchor
            anchorWaitJob = scope.launch {
                val rect = withTimeoutOrNull(anchorTimeoutMs) {
                    registry.rects.first { it[anchor] != null }[anchor]
                }
                val current = _state.value as? TourState.Running ?: return@launch
                if (current.index != index) return@launch // a later step won the race
                _state.value = if (rect != null) {
                    current.copy(anchor = rect, degraded = false)
                } else {
                    current.copy(degraded = true)
                }
            }
        }
    }

    private fun isFirstOccurrenceOfScreen(screen: TourScreen, index: Int): Boolean =
        steps.subList(0, index).none { it.screen == screen }

    /**
     * Entering Home/Library/Downloaded/Account switches the bottom tab (and,
     * the very first time Library appears, also opens the picked pair).
     * Entering Sheet/Details/Reader/Player navigates nothing — that
     * navigation is the real screen transition the user's own guided tap (or
     * Track C's reader/player code) just performed.
     */
    private fun emitEnterNav(step: TourStep, previousScreen: TourScreen?, isFirstOfScreen: Boolean) {
        if (step.screen == previousScreen) return
        val tab = tabRouteFor(step.screen) ?: return
        _nav.tryEmit(TourNav.GoToTab(tab))
        if (step.screen == TourScreen.Library && isFirstOfScreen) {
            pairId?.let { _nav.tryEmit(TourNav.OpenLibraryAt(it)) }
        }
    }

    /** Route constants duplicated as literals (not imported from `ui.Routes`) to keep this
     *  plain-Kotlin engine free of a dependency on the Compose nav layer; they must stay in
     *  sync with `Routes.HOME` / `Routes.LIBRARY` / `Routes.DOWNLOADED` / `Routes.ACCOUNT` in
     *  `ui/BookSyncNavigation.kt`, which is what actually executes a [TourNav.GoToTab].
     */
    private fun tabRouteFor(screen: TourScreen): String? = when (screen) {
        TourScreen.Home -> "home"
        TourScreen.Library -> "library"
        TourScreen.Downloaded -> "downloaded"
        TourScreen.Account -> "account"
        TourScreen.Sheet, TourScreen.Details, TourScreen.Reader, TourScreen.Player -> null
    }

    private fun isReaderOrPlayer(screen: TourScreen) =
        screen == TourScreen.Reader || screen == TourScreen.Player

    private fun buildSteps(pickedPairId: Int?): List<TourStep> {
        if (pickedPairId != null) return TOUR
        val firstNeedsPairIndex = TOUR.indexOfFirst { it.needsPair }
        if (firstNeedsPairIndex < 0) return TOUR
        val before = TOUR.subList(0, firstNeedsPairIndex)
        val after = TOUR.filterIndexed { i, step -> i > firstNeedsPairIndex && !step.needsPair }
        return before + NO_PAIR_SKIP_STEP + after
    }

    private fun finish() {
        val running = _state.value as? TourState.Running
        if (running != null && isReaderOrPlayer(running.step.screen)) {
            _nav.tryEmit(TourNav.PopToMain)
        }
        anchorWaitJob?.cancel()
        _state.value = TourState.Finished
        scope.launch { prefs.markCompleted() }
    }
}
