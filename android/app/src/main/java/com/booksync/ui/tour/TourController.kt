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
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/** Where the tour is right now. */
/** How long the "tap the page" reader step waits, once the reader has settled, before raising the bars itself. */
const val READER_BARS_GRACE_MS = 4_000L

/**
 * Whether a step's anchor (or, for `details_maintenance`, one of its
 * [TourStep.altAnchors]) has shown up (issue #642).
 *
 * Replaces the old fixed 1.5 s timeout, which counted from step entry
 * regardless of whether the screen had actually finished loading — a card
 * would say a control was missing, then the screen would draw it a moment
 * later, and the verdict never revisited itself.
 */
enum class AnchorResolution { Pending, Found, Missing }

sealed class TourState {
    data object Idle : TourState()

    data class Running(
        val index: Int,
        val step: TourStep,
        /** The spotlighted control's window bounds. Null whenever [resolution] isn't [AnchorResolution.Found]. */
        val anchor: Rect?,
        /** [step]'s anchor resolution (issue #642) — see [AnchorResolution]. */
        val resolution: AnchorResolution,
        /**
         * Which of [TourStep.anchor] / [TourStep.altAnchors] is actually spotlighted
         * (issue #642) — null unless [resolution] is [AnchorResolution.Found]. The
         * overlay follows this anchor's *live* rect rather than [anchor], which is
         * only a snapshot from whenever the resolution last changed.
         */
        val spotlighted: TourAnchor?,
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
        /**
         * True from the moment [TourController.start] enters step 0 until the picked pair
         * (or the lack of one) is known (issue #642, #641) — the welcome card renders at
         * once rather than waiting on the library, but [TourController.next] does nothing
         * until this clears, since the step list itself isn't final yet.
         */
        val preparing: Boolean = false,
    ) : TourState() {
        /**
         * True once [step]'s anchor has waited this tour out without showing up. A step
         * with no anchor at all (a plain info card) is never degraded — see [AnchorResolution].
         */
        val degraded: Boolean get() = resolution == AnchorResolution.Missing
    }

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
 * The walkthrough's state machine (issue #597 §3, revised by #642). Plain
 * Kotlin — no Android types beyond `androidx.compose.ui.geometry.Rect`, which
 * is a pure value class — so it is fully covered by JVM tests; the reader is
 * a second Activity, so this has to be app-scoped rather than living in a
 * MainActivity ViewModel (see `di/AppModule.kt`'s `provideTourController`,
 * which is where the [clock]/[settleMs]/[hardCapMs]/[awaitLibrary] defaults
 * actually apply — Dagger's generated factory does not honour Kotlin default
 * arguments, so a plain `@Provides` function calls this constructor instead
 * of an `@Inject constructor`).
 */
class TourController(
    private val registry: TourAnchorRegistry,
    private val prefs: TourPrefs,
    private val picker: TourPairPicker,
    private val scope: CoroutineScope,
    /** Used only to log how long each anchor resolution took; the resolution logic itself
     *  is driven by coroutine `delay`s under [scope], which is what tests control via
     *  `advanceTimeBy` / `advanceUntilIdle`. */
    private val clock: () -> Long = System::currentTimeMillis,
    /**
     * How long an absent anchor stays [AnchorResolution.Pending] on a *settled* screen
     * before resolving to [AnchorResolution.Missing] (issue #642). Restarts whenever the
     * screen's settled flag or the anchor's presence changes.
     */
    private val settleMs: Long = 600,
    /**
     * The longest an absent anchor stays [AnchorResolution.Pending] on a screen that never
     * reports settled (issue #642) — a bound against a screen that never gets there, not the
     * ordinary path. Applies only before the step's first [AnchorResolution.Found]; once an
     * anchor has shown up, a later vanish waits indefinitely, since that almost always means
     * the user's tap landed and the next screen is on its way in.
     *
     * 30 s rather than 10: every screen reports settled, so this is only a safety net, and the
     * reader's honest Pending window (Activity created to navigator ready) measured 8 to 17 s
     * on the emulator. A cap that close would flip a slow phone to Missing and back again.
     */
    private val hardCapMs: Long = 30_000,
    /**
     * Waits (bounded by [libraryWaitMs]) for the library's pairs to be loaded (issue #641)
     * before [start] asks [TourPairPicker.pick] to choose one — a library that hasn't synced
     * yet would otherwise look exactly like an empty one. The boolean return isn't branched
     * on here: [TourPairPicker.pick] already handles "nothing synced yet" by returning null,
     * which [buildSteps] turns into the skip card either way.
     */
    private val awaitLibrary: suspend (Long) -> Boolean = { true },
    /** A minute, not 20 s (issue #652): the first pairs fetch for a library of a few hundred
     *  took 23 s on the emulator, and the welcome card is already on screen while this runs. */
    private val libraryWaitMs: Long = 60_000,
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
    private var anchorWatchJob: Job? = null
    private var barsGraceJob: Job? = null
    private var startJob: Job? = null
    /** Bumped by every [start], so a stale `start()` coroutine that resumes late (its
     *  [awaitLibrary] having finally returned) can tell it was superseded and must not
     *  overwrite whatever the newer `start()` put in place. */
    private var startGeneration: Int = 0
    /** When the current step's anchor was entered — [clock]-based, for the transition log only. */
    private var stepEnteredAtMs: Long = 0
    /** The anchor last reported to [TourAnchorRegistry.setWanted] for the current step's
     *  resolution, kept separately from `_state` so a resolution that doesn't change the
     *  spotlighted anchor doesn't re-announce it. */
    private var lastSpotlighted: TourAnchor? = null

    /** Picks a pair (degrading gracefully to the skip card when none qualifies) and enters step 0. */
    fun start() {
        anchorWatchJob?.cancel()
        barsGraceJob?.cancel()
        startJob?.cancel()
        val generation = ++startGeneration
        steps = TOUR
        pairId = null
        willCleanUp = false
        // "Replay the walkthrough" (issue #597 follow-up) launches from wherever the
        // Account tab happens to be, and PR #614 stopped the tour switching tabs on its
        // own for every other step — without this, step 0's Home anchors would render
        // over whatever screen was already showing. The only other automatic switch is
        // [emitPopToMain], leaving the reader/player block.
        _nav.tryEmit(TourNav.GoToTab(requireNotNull(tabRouteFor(TourScreen.Home))))
        // Shows the welcome card at once (issue #642) rather than waiting on the library
        // — nothing about step 0 depends on the picked pair, and a blank screen while a
        // slow connection loads pairs is worse than a Next button that briefly says
        // "Getting your library…".
        enter(0, preparing = true)
        startJob = scope.launch {
            awaitLibrary(libraryWaitMs)
            val picked = picker.pick()
            val untouched = picked?.let { picker.isUntouched(it) } ?: false
            // A second start() (e.g. quit-and-replay while this one was still preparing)
            // bumped the generation already; this resume is stale and must not stomp on
            // whatever that second start() put in place.
            if (generation != startGeneration) return@launch
            pairId = picked
            willCleanUp = untouched
            steps = buildSteps(picked)
            val current = _state.value as? TourState.Running ?: return@launch
            if (current.index != 0) return@launch
            _state.value = current.copy(
                preparing = false,
                pairId = pairId,
                total = steps.size,
                willCleanUp = willCleanUp,
            )
        }
    }

    /** Ignored while [TourState.Running.preparing] — the step list isn't final yet — and on
     *  a [Advance.TapAnchor] step, where the card says to tap the highlighted control instead. */
    fun next() {
        val running = _state.value as? TourState.Running ?: return
        if (running.preparing) return
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

    private fun enter(index: Int, preparing: Boolean = false) {
        anchorWatchJob?.cancel()
        barsGraceJob?.cancel()
        val step = steps[index]
        registry.setWanted(step.anchor)
        emitEnterNav(step, isFirstOccurrenceOfScreen(step.screen, index))

        // The "tap the page" step waits for the reader's bars; a user who never
        // taps would be stuck, so after a grace period — counted from the
        // reader actually settling, not from step entry (issue #642: the old
        // 4 s counted from Activity creation, before the book had loaded) —
        // the tour raises them itself and the resulting ReaderBarsShown
        // advances the step as if the user had tapped.
        val waitsForBars = (step.advance as? Advance.WaitFor)?.event == TourEvent.ReaderBarsShown
        if (waitsForBars) {
            barsGraceJob = scope.launch {
                registry.settled.first { TourScreen.Reader in it }
                delay(READER_BARS_GRACE_MS)
                val current = _state.value as? TourState.Running ?: return@launch
                if (current.index == index) _nav.tryEmit(TourNav.ShowReaderBars)
            }
        }

        stepEnteredAtMs = clock()
        lastSpotlighted = null

        val candidates = if (step.anchor != null) listOf(step.anchor) + step.altAnchors else emptyList()
        val immediate = candidates.firstOrNull { registry.rects.value[it] != null }
        val initialResolution = if (step.anchor == null || immediate != null) {
            AnchorResolution.Found
        } else {
            AnchorResolution.Pending
        }
        if (immediate != null) {
            lastSpotlighted = immediate
            registry.setWanted(immediate)
        }

        _state.value = TourState.Running(
            index = index,
            step = step,
            anchor = immediate?.let { registry.rects.value[it] },
            resolution = initialResolution,
            spotlighted = immediate,
            pairId = pairId,
            total = steps.size,
            canGoBack = canGoBack(index),
            willCleanUp = willCleanUp,
            preparing = preparing,
        )

        if (step.anchor != null) {
            watchAnchor(index, step, candidates, alreadyFound = immediate != null)
        }
    }

    /**
     * Re-evaluates [step]'s anchor resolution for as long as [index] stays current (issue
     * #642) — cancelled the moment [enter] moves on, by the `anchorWatchJob?.cancel()` at its
     * top. `collectLatest` over the two facts the decision actually depends on — which
     * candidate anchor (if any) is present, and whether the screen has settled — is what gives
     * the debounce its "restarts on any change" behaviour for free: a new emission from either
     * source cancels whatever `delay` the previous emission was sitting in.
     *
     * Deliberately reduced to that pair *before* `collectLatest`, rather than combining the raw
     * [TourAnchorRegistry.rects] map straight in (issue #642 follow-up, PR review): that map
     * re-emits on every layout pass of *any* tagged control, so a scrolling list — or anything
     * else animating — changes it every frame. Feeding the raw map to `collectLatest` restarted
     * the settle/hard-cap delay on every one of those frames, so an absent anchor could never
     * reach [AnchorResolution.Missing] on a busy screen; and once [AnchorResolution.Found], it
     * rewrote `_state` with a new rect (and logged a transition) once per frame, recomposing
     * every screen collecting the tour state. [distinctUntilChanged] means a rect that merely
     * moves — without changing which anchor is found, or the screen's settled flag — produces no
     * emission at all.
     */
    private fun watchAnchor(
        index: Int,
        step: TourStep,
        candidates: List<TourAnchor>,
        alreadyFound: Boolean,
    ) {
        var everFound = alreadyFound
        anchorWatchJob = scope.launch {
            combine(registry.rects, registry.settled, registry.loading) { rects, settled, loading ->
                Triple(
                    candidates.firstOrNull { rects[it] != null },
                    step.screen in settled,
                    step.screen in loading,
                )
            }
                .distinctUntilChanged()
                .collectLatest { (foundAnchor, settledNow, loadingNow) ->
                    if (foundAnchor != null) {
                        everFound = true
                        // A snapshot at the transition, not a live-tracking value: the overlay
                        // already follows the live rect itself via the registry.
                        val rect = registry.rects.value[foundAnchor]
                        applyResolution(index, step, AnchorResolution.Found, rect, foundAnchor)
                        return@collectLatest
                    }
                    if (everFound) {
                        // The hard cap applies only before the first Found: past that point a
                        // vanished anchor almost always means the user's tap landed and the
                        // next screen is on its way in, so this waits indefinitely for the
                        // step's own expected event to advance it instead.
                        applyResolution(index, step, AnchorResolution.Pending, null, null)
                        return@collectLatest
                    }
                    applyResolution(index, step, AnchorResolution.Pending, null, null)
                    // A screen that says it is still loading is waited for, however long
                    // that takes (issue #652) — the cap is only for a screen nobody has
                    // heard from. The Pending card keeps its quit button throughout.
                    if (loadingNow && !settledNow) return@collectLatest
                    delay(if (settledNow) settleMs else hardCapMs)
                    applyResolution(index, step, AnchorResolution.Missing, null, null)
                }
        }
    }

    private fun applyResolution(
        index: Int,
        step: TourStep,
        resolution: AnchorResolution,
        rect: Rect?,
        spotlighted: TourAnchor?,
    ) {
        val current = _state.value as? TourState.Running ?: return
        if (current.index != index) return
        val changed = current.resolution != resolution ||
            current.anchor != rect ||
            current.spotlighted != spotlighted
        if (!changed) return
        if (spotlighted != null && spotlighted != lastSpotlighted) {
            lastSpotlighted = spotlighted
            registry.setWanted(spotlighted)
        }
        val old = current.resolution
        _state.value = current.copy(resolution = resolution, anchor = rect, spotlighted = spotlighted)
        // Only a change of verdict is worth a line; a step that enters already Found and then
        // picks up its rect logged a meaningless "Found→Found".
        if (old != resolution) {
            val elapsedMs = clock() - stepEnteredAtMs
            android.util.Log.d("Tour", "${step.id}: $old→$resolution after $elapsedMs ms")
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
        anchorWatchJob?.cancel()
        barsGraceJob?.cancel()
        startJob?.cancel()
        registry.setWanted(null)
        _state.value = TourState.Finished
        scope.launch { prefs.markCompleted() }
    }
}
