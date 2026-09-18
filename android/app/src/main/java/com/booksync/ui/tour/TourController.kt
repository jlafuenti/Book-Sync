package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
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
/** How long the "tap the page" reader step waits before raising the bars itself. */
const val READER_BARS_GRACE_MS = 4_000L

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
        /** False on step 0 and on the first step of a new screen (see [TourController.back]). */
        val canGoBack: Boolean = false,
        /**
         * True when [pairId] was untouched — see [TourPairPicker.isUntouched] — the moment
         * the tour started or last adopted it, meaning [TourController] will emit
         * [TourNav.CleanUp] for it when the tour finishes or is quit (issue #597 tester
         * feedback: the welcome card promised no trace, but the book the tour opened kept
         * showing up in Continue Reading and Downloaded afterward).
         */
        val willCleanUp: Boolean = false,
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
    /**
     * Undo whatever the tour did to [pairId] (issue #597 tester feedback): reset its server
     * and local progress, drop its cached files and sync map, and stop the player if it
     * still holds this pair's media. Emitted by [TourController.finish] only when the pair
     * was untouched before the tour opened it.
     */
    data class CleanUp(val pairId: Int) : TourNav()
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
    /** See [TourState.Running.willCleanUp]; carried here so [enter] can stamp every step's
     *  state with it without recomputing it on every `next()`/`back()`. */
    private var willCleanUp: Boolean = false
    private var anchorWaitJob: Job? = null
    private var barsGraceJob: Job? = null

    /** Picks a pair (degrading gracefully to the skip card when none qualifies) and enters step 0. */
    fun start() {
        anchorWaitJob?.cancel()
        scope.launch {
            val picked = picker.pick()
            pairId = picked
            willCleanUp = picked?.let { picker.isUntouched(it) } ?: false
            steps = buildSteps(picked)
            // "Replay the walkthrough" (issue #597 follow-up) launches from wherever the
            // Account tab happens to be, and PR #614 stopped the tour switching tabs on its
            // own for every other step — without this, step 0's Home anchors would render
            // over whatever screen was already showing. The only other automatic switch is
            // [emitPopToMain], leaving the reader/player block.
            _nav.tryEmit(TourNav.GoToTab(requireNotNull(tabRouteFor(TourScreen.Home))))
            enter(0)
        }
    }

    /** Ignored on a [Advance.TapAnchor] step — the card says to tap the highlighted control instead. */
    fun next() {
        val running = _state.value as? TourState.Running ?: return
        if (running.step.advance is Advance.TapAnchor) return
        advanceFrom(running.index)
    }

    /**
     * Steps back within the current screen only. Across a screen boundary the
     * previous step's control is gone (the sheet closed, the reader finished),
     * so it would land on a "tap this" card that can never advance.
     */
    fun back() {
        val running = _state.value as? TourState.Running ?: return
        if (!canGoBack(running.index)) return
        enter(running.index - 1)
    }

    private fun canGoBack(index: Int): Boolean =
        index > 0 && steps[index - 1].screen == steps[index].screen

    /** Quits the tour from wherever it is right now. */
    fun quit() = finish()

    /**
     * A screen found a better pair than the picker's (the Library adopts the
     * first synced pair in its own ordering rather than scrolling to an
     * arbitrary one); every later step follows it. Also re-evaluates
     * [TourPairPicker.isUntouched] for the newly adopted pair (issue #597 tester
     * feedback) — the picker's original pick and the Library's substitute can
     * disagree on whether cleanup applies.
     */
    fun adoptPair(newPairId: Int) {
        pairId = newPairId
        val running = _state.value as? TourState.Running ?: return
        _state.value = running.copy(pairId = newPairId)
        scope.launch {
            val untouched = picker.isUntouched(newPairId)
            if (pairId != newPairId) return@launch // superseded by a later adopt, or a fresh start
            willCleanUp = untouched
            val current = _state.value as? TourState.Running ?: return@launch
            if (current.pairId == newPairId) {
                _state.value = current.copy(willCleanUp = untouched)
            }
        }
    }

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
        if (event is TourEvent.SheetClosed) {
            // The user swiped the sheet away mid-step: its controls are gone,
            // so go back to the step that asks them to open it. Searched only
            // *before* the current step — `library_tap_downloaded` (issue #597
            // follow-up) is also a Library-screen TapAnchor step, just one that
            // comes later in the script, so an unbounded search would find that
            // one instead of `library_open_pair` once the tour has moved past it.
            if (running.step.screen == TourScreen.Sheet) {
                val reopen = steps.subList(0, running.index)
                    .indexOfLast { it.screen == TourScreen.Library && it.advance is Advance.TapAnchor }
                if (reopen >= 0) enter(reopen)
            }
            return
        }
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
            emitPopToMain()
        }
        enter(nextIndex)
    }

    private fun enter(index: Int) {
        anchorWaitJob?.cancel()
        barsGraceJob?.cancel()
        val step = steps[index]
        registry.setWanted(step.anchor)
        emitEnterNav(step, isFirstOccurrenceOfScreen(step.screen, index))

        // The "tap the page" step waits for the reader's bars; a user who never
        // taps would be stuck, so after a grace period the tour raises them
        // itself and the resulting ReaderBarsShown advances the step.
        val waitsForBars = (step.advance as? Advance.WaitFor)?.event == TourEvent.ReaderBarsShown
        if (waitsForBars) {
            barsGraceJob = scope.launch {
                delay(READER_BARS_GRACE_MS)
                val current = _state.value as? TourState.Running ?: return@launch
                if (current.index == index) _nav.tryEmit(TourNav.ShowReaderBars)
            }
        }

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
            canGoBack = canGoBack(index),
            willCleanUp = willCleanUp,
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
     * Switching bottom tabs is a guided tap like every other control the tour
     * spotlights (issue #597 follow-up: the tour used to switch tabs itself,
     * which tester feedback flagged as inconsistent with that rule) — a
     * `home_tap_library` / `library_tap_downloaded` / `downloaded_tap_account`
     * step asks the user to tap the tab, and the resulting [TourEvent.RouteShown]
     * is what actually advances the tour. So entering a step never switches
     * tabs on its own; the one thing it still does is open the picked pair the
     * very first time Library appears, since that isn't a tab switch — the
     * user is already looking at the Library screen they just tapped into.
     */
    private fun emitEnterNav(step: TourStep, isFirstOfScreen: Boolean) {
        if (step.screen == TourScreen.Library && isFirstOfScreen) {
            pairId?.let { _nav.tryEmit(TourNav.OpenLibraryAt(it)) }
        }
    }

    /**
     * The other automatic tab switch, besides [start]'s own one-time hop to Home: leaving
     * the reader/player block. The reader can be opened from any tab (Home's Continue
     * Reading, Library's card, or hopping back from the player), but the script always
     * resumes on Library right after (the Filters step), so this always lands there
     * regardless of which tab was active before the reader opened.
     */
    private fun emitPopToMain() {
        _nav.tryEmit(TourNav.PopToMain)
        _nav.tryEmit(TourNav.GoToTab(requireNotNull(tabRouteFor(TourScreen.Library))))
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
            emitPopToMain()
        }
        if (running?.willCleanUp == true && running.pairId != null) {
            _nav.tryEmit(TourNav.CleanUp(running.pairId))
        }
        anchorWaitJob?.cancel()
        barsGraceJob?.cancel()
        registry.setWanted(null)
        _state.value = TourState.Finished
        scope.launch { prefs.markCompleted() }
    }
}
