package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The walkthrough's state machine (issue #597 §3, revised by #642): start picks a pair and
 * enters step 0; `next`/`back` walk the script; a `TapAnchor` step advances only on its
 * expected event and ignores `next()`; a step's anchor resolves to `Found`, `Pending` or
 * `Missing` based on whether the control has shown up and whether its screen has settled,
 * re-evaluated for as long as the step is current rather than judged once on a fixed timer;
 * a library with no synced pair collapses every `needsPair` step into one skip card;
 * quitting — or the final `next()` — finishes the tour and records it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TourControllerTest {

    private val prefs = mockk<TourPrefs>(relaxed = true)
    private val picker = mockk<TourPairPicker>()
    private lateinit var registry: TourAnchorRegistry

    /**
     * Unconfined, but sharing the [TestScope]'s scheduler: launched bodies run
     * eagerly (so `state.value` is current the moment a call returns) while
     * `delay()`-based waits still obey `advanceTimeBy` / `advanceUntilIdle`
     * rather than real wall-clock time.
     *
     * Tests that assert on [TourController.nav] must collect it through this
     * *same* scope (not `backgroundScope`, which runs on the `TestScope`'s own
     * `StandardTestDispatcher`) — otherwise the collector is merely scheduled,
     * not yet attached, by the time an eagerly-run `start()`/`next()` emits.
     */
    private fun TestScope.unconfinedScope(): CoroutineScope =
        CoroutineScope(UnconfinedTestDispatcher(testScheduler))

    private fun TestScope.newController(
        pairId: Int? = 42,
        scope: CoroutineScope = unconfinedScope(),
        awaitLibrary: suspend (Long) -> Boolean = { true },
    ): TourController {
        registry = TourAnchorRegistry()
        coEvery { picker.pick() } returns pairId
        // Untouched by default so existing tests don't have to care; tests that exercise
        // CleanUp override this for the specific pair id they care about.
        coEvery { picker.isUntouched(any()) } returns false
        return TourController(
            registry = registry,
            prefs = prefs,
            picker = picker,
            scope = scope,
            awaitLibrary = awaitLibrary,
        )
    }

    private fun running(controller: TourController): TourState.Running =
        controller.state.value as TourState.Running

    /** Synthesizes whatever event a step's advance rule is waiting for. */
    private fun eventFor(expected: TourEvent): TourEvent = when (expected) {
        is TourEvent.SheetOpened -> TourEvent.SheetOpened(42)
        is TourEvent.DetailsOpened -> TourEvent.DetailsOpened(42)
        is TourEvent.ReaderOpened -> TourEvent.ReaderOpened(42)
        TourEvent.ReaderBarsShown -> TourEvent.ReaderBarsShown
        TourEvent.ReaderSyncedSelection -> TourEvent.ReaderSyncedSelection
        TourEvent.SheetClosed -> TourEvent.SheetClosed
        is TourEvent.PlayerOpened -> TourEvent.PlayerOpened(42)
        is TourEvent.RouteShown -> expected
        is TourEvent.AnchorTapped -> expected
    }

    /** Drives the controller step by step (Next, or the matching event) until [stepId] is current. */
    private fun TourController.advanceUntil(stepId: String) {
        var guard = 0
        while (running(this).step.id != stepId) {
            when (val advance = running(this).step.advance) {
                Advance.Next -> next()
                is Advance.TapAnchor -> onEvent(eventFor(advance.expect))
                is Advance.WaitFor -> onEvent(eventFor(advance.event))
            }
            check(++guard < 100) { "advanceUntil($stepId) looped past the whole script" }
        }
    }

    @Test
    fun `start enters step 0 and switches to the Home tab once`() = runTest {
        // Issue #597 follow-up: "Replay the walkthrough" launches from the Account
        // tab, and PR #614 stopped the tour switching tabs on its own for every
        // other step — so without this, step 0's Home anchors would render over
        // whatever tab was already showing.
        val scope = unconfinedScope()
        val controller = newController(pairId = 42, scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }

        controller.start()
        advanceUntilIdle()

        val state = running(controller)
        assertEquals(0, state.index)
        assertEquals(TOUR[0].id, state.step.id)
        assertEquals(42, state.pairId)
        assertEquals(TOUR.size, state.total)
        assertEquals(listOf(TourNav.GoToTab("home")), navLog)
    }

    @Test
    fun `back at step 0 is a no-op`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()

        controller.back()

        assertEquals(0, running(controller).index)
    }

    @Test
    fun `next walks forward and back walks backward`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()

        controller.next()
        controller.next()
        assertEquals(2, running(controller).index)

        controller.back()
        assertEquals(1, running(controller).index)
    }

    @Test
    fun `back never crosses a screen boundary`() = runTest {
        // Stepping back from the first Details card would land on "tap View
        // details" with the sheet already closed — a card that cannot advance.
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("details_chips")
        val index = running(controller).index
        assertFalse(running(controller).canGoBack)

        controller.back()

        assertEquals(index, running(controller).index)
        // Within a screen it still works.
        controller.next()
        assertTrue(running(controller).canGoBack)
        controller.back()
        assertEquals(index, running(controller).index)
    }

    @Test
    fun `next is ignored on a TapAnchor step`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("library_open_pair")
        val indexBefore = running(controller).index

        controller.next()

        assertEquals(indexBefore, running(controller).index)
        assertTrue(running(controller).step.advance is Advance.TapAnchor)
    }

    @Test
    fun `a TapAnchor step advances only on its expected event`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("library_open_pair")
        val indexBefore = running(controller).index

        controller.onEvent(TourEvent.ReaderBarsShown) // unrelated
        assertEquals(indexBefore, running(controller).index)

        controller.onEvent(TourEvent.SheetOpened(42))
        assertEquals(indexBefore + 1, running(controller).index)
        assertEquals("sheet_stream", running(controller).step.id)
    }

    @Test
    fun `a WaitFor step ignores unrelated events and advances only on its own`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_tap_page")

        controller.onEvent(TourEvent.ReaderSyncedSelection) // wrong event for this step
        assertEquals("reader_tap_page", running(controller).step.id)

        controller.onEvent(TourEvent.ReaderBarsShown)
        assertEquals("reader_switch_to_audio", running(controller).step.id)
    }

    @Test
    fun `the reader bars grace does not start until the reader has settled`() = runTest {
        // Issue #642: the old 4 s grace counted from the reader Activity's own
        // creation, before the book had loaded — a slow load could raise the
        // bars itself before the user had anything to look at yet.
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_tap_page")

        advanceTimeBy(60_000)
        assertFalse(navLog.contains(TourNav.ShowReaderBars))

        // Not advanceUntilIdle() here: with only the grace-period delay left
        // pending, that would drain the scheduler by running the delay to
        // completion outright, rather than leaving it for advanceTimeBy below
        // to cross a specific number of milliseconds at a time.
        registry.setSettled(TourScreen.Reader, true)

        advanceTimeBy(READER_BARS_GRACE_MS - 1)
        assertFalse(navLog.contains(TourNav.ShowReaderBars))

        advanceTimeBy(2)
        assertTrue(navLog.contains(TourNav.ShowReaderBars))
    }

    @Test
    fun `leaving the reader tap-page step first cancels the bars grace request`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_tap_page")
        registry.setSettled(TourScreen.Reader, true)

        controller.onEvent(TourEvent.ReaderBarsShown)
        advanceTimeBy(READER_BARS_GRACE_MS + 10)

        assertFalse(navLog.contains(TourNav.ShowReaderBars))
    }

    @Test
    fun `a screen can adopt a different pair for the rest of the tour`() = runTest {
        val controller = newController(pairId = 42)
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("library_open_pair")

        controller.adoptPair(7)

        assertEquals(7, running(controller).pairId)
        controller.onEvent(TourEvent.SheetOpened(7))
        assertEquals(7, running(controller).pairId)
    }

    @Test
    fun `dismissing the sheet mid-step returns to the open-a-book step`() = runTest {
        // Also guards against a regression from the tap-the-tab steps (issue
        // #597 follow-up): `library_tap_downloaded` is a later Library-screen
        // TapAnchor step too, so the reopen search must find `library_open_pair`
        // specifically, not just "the last Library TapAnchor step in the script".
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("sheet_download")

        controller.onEvent(TourEvent.SheetClosed)

        assertEquals("library_open_pair", running(controller).step.id)
        // Elsewhere the event is ignored.
        controller.onEvent(TourEvent.SheetClosed)
        assertEquals("library_open_pair", running(controller).step.id)
    }

    @Test
    fun `skip on a non-skippable WaitFor step does nothing`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_tap_page")

        controller.skip()

        assertEquals("reader_tap_page", running(controller).step.id)
    }

    @Test
    fun `skip on the reader selection step advances and asks for the toolbar sync`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil(READER_SELECTION_STEP_ID)

        controller.skip()

        assertEquals("player_paused", running(controller).step.id)
        assertTrue(navLog.contains(TourNav.SkipToToolbarSync))
    }

    // ---- anchor resolution (issue #642) ----

    @Test
    fun `a step with no anchor at all is Found immediately`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()

        val state = running(controller)
        assertNull(state.step.anchor) // home_welcome
        assertEquals(AnchorResolution.Found, state.resolution)
        assertFalse(state.degraded)
    }

    @Test
    fun `an anchor rect already present when the step is entered is Found immediately`() = runTest {
        val controller = newController()
        registry.set(TourAnchor.HomeContinueReading, Rect(0f, 0f, 10f, 10f))
        controller.start()
        advanceUntilIdle()

        controller.next() // -> home_continue_reading

        val state = running(controller)
        assertEquals(AnchorResolution.Found, state.resolution)
        assertFalse(state.degraded)
        assertEquals(Rect(0f, 0f, 10f, 10f), state.anchor)
    }

    @Test
    fun `an anchor that registers well before the hard cap on an unsettled screen never goes Missing`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading; screen never reports settled

        advanceTimeBy(3_000)
        assertEquals(AnchorResolution.Pending, running(controller).resolution)

        registry.set(TourAnchor.HomeContinueReading, Rect(0f, 0f, 10f, 10f))
        advanceUntilIdle()

        val state = running(controller)
        assertEquals(AnchorResolution.Found, state.resolution)
        assertFalse(state.degraded)
        assertEquals(Rect(0f, 0f, 10f, 10f), state.anchor)
    }

    @Test
    fun `an absent anchor on a settled screen resolves Missing at 600ms, not before`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading
        // Not advanceUntilIdle() here: with only the settle-window delay left
        // pending, that would drain the scheduler by running the delay to
        // completion outright, rather than leaving it for advanceTimeBy below
        // to cross a specific number of milliseconds at a time.
        registry.setSettled(TourScreen.Home, true)

        advanceTimeBy(599)
        assertEquals(AnchorResolution.Pending, running(controller).resolution)

        advanceTimeBy(2)
        assertEquals(AnchorResolution.Missing, running(controller).resolution)
        assertTrue(running(controller).degraded)
        assertNull(running(controller).anchor)
    }

    @Test
    fun `Missing recovers to Found once the anchor registers`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next()
        registry.setSettled(TourScreen.Home, true)
        advanceTimeBy(600)
        advanceUntilIdle()
        assertEquals(AnchorResolution.Missing, running(controller).resolution)

        registry.set(TourAnchor.HomeContinueReading, Rect(1f, 1f, 2f, 2f))
        advanceUntilIdle()

        val state = running(controller)
        assertEquals(AnchorResolution.Found, state.resolution)
        assertFalse(state.degraded)
        assertEquals(Rect(1f, 1f, 2f, 2f), state.anchor)
    }

    @Test
    fun `an absent anchor on an unsettled screen resolves Missing only at the 30s hard cap`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading, screen never reports settled

        // 30 s, not 10 (issue #642 follow-up): every screen reports settled, so the cap is only
        // a safety net, and the reader's honest Pending window -- Activity created to navigator
        // ready -- measured 8 to 17 s on the emulator. A cap that close would flip a slow phone to
        // Missing and back, which is the very thing this engine exists to stop.
        advanceTimeBy(29_999)
        assertEquals(AnchorResolution.Pending, running(controller).resolution)

        advanceTimeBy(2)
        assertEquals(AnchorResolution.Missing, running(controller).resolution)
    }

    @Test
    fun `the hard cap applies only before the first Found -- a later vanish waits indefinitely`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next()
        registry.set(TourAnchor.HomeContinueReading, Rect(0f, 0f, 10f, 10f))
        advanceUntilIdle()
        assertEquals(AnchorResolution.Found, running(controller).resolution)

        registry.clear(TourAnchor.HomeContinueReading)
        advanceUntilIdle()
        assertEquals(AnchorResolution.Pending, running(controller).resolution)
        assertNull(running(controller).anchor)

        advanceTimeBy(60_000)
        assertEquals(AnchorResolution.Pending, running(controller).resolution)
    }

    // ---- rect churn from unrelated anchors must not disturb the decision (issue #642 follow-up) ----
    //
    // registry.rects re-emits on every layout pass of ANY tagged control, so a scrolling list
    // (or anything else animating) changes it every frame. watchAnchor must react only to
    // whether *this step's* candidate anchor is present and whether its screen is settled —
    // not to the raw map — or an absent anchor could never reach Missing on a busy screen, and
    // a Found step would rewrite state (and log a transition) every single frame.

    @Test
    fun `unrelated rect churn on a settled screen does not restart the settle delay`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading; its own anchor never registers
        registry.setSettled(TourScreen.Home, true)

        // Churn some other anchor's rect every 100ms for 2s -- well past the 600ms settle
        // window -- as a scrolling list would while this step's own anchor stays absent.
        repeat(20) { i ->
            advanceTimeBy(100)
            runCurrent()
            registry.set(TourAnchor.TabLibrary, Rect(0f, i.toFloat(), 10f, i + 10f))
            runCurrent()
        }

        assertEquals(AnchorResolution.Missing, running(controller).resolution)
    }

    @Test
    fun `unrelated rect churn on an unsettled screen does not restart the hard cap`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading; screen never reports settled

        // Churn some other anchor's rect every second for 31s -- well past the 30s hard cap.
        repeat(31) { i ->
            advanceTimeBy(1_000)
            runCurrent()
            registry.set(TourAnchor.TabLibrary, Rect(0f, i.toFloat(), 10f, i + 10f))
            runCurrent()
        }

        assertEquals(AnchorResolution.Missing, running(controller).resolution)
    }

    @Test
    fun `moving the found anchor's rect produces no further state emissions`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading
        registry.set(TourAnchor.HomeContinueReading, Rect(0f, 0f, 10f, 10f))
        advanceUntilIdle()
        assertEquals(AnchorResolution.Found, running(controller).resolution)

        val states = mutableListOf<TourState>()
        scope.launch { controller.state.collect { states.add(it) } }
        // A StateFlow collector receives the current value immediately on
        // subscribe; drop that one so only emissions caused by what follows count.
        states.clear()

        // As the control that's already spotlighted scrolls, its rect changes every
        // frame; none of that should touch `_state` once the step has resolved Found.
        repeat(20) { i ->
            registry.set(TourAnchor.HomeContinueReading, Rect(0f, i.toFloat(), 10f, i + 10f))
            runCurrent()
        }

        assertTrue(states.isEmpty())
    }

    @Test
    fun `an alternate anchor is spotlighted when the primary is absent`() = runTest {
        val controller = newController()
        registry.set(TourAnchor.DetailsRefreshSync, Rect(0f, 0f, 5f, 5f))
        controller.start()
        advanceUntilIdle()

        controller.advanceUntil("details_maintenance")

        val state = running(controller)
        assertEquals(AnchorResolution.Found, state.resolution)
        assertEquals(TourAnchor.DetailsRefreshSync, state.spotlighted)
        assertEquals(TourAnchor.DetailsRefreshSync, registry.wanted.value)
    }

    @Test
    fun `the spotlight moves to the primary anchor once it appears`() = runTest {
        val controller = newController()
        registry.set(TourAnchor.DetailsRefreshSync, Rect(0f, 0f, 5f, 5f))
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("details_maintenance")
        assertEquals(TourAnchor.DetailsRefreshSync, running(controller).spotlighted)

        registry.set(TourAnchor.DetailsUnlink, Rect(1f, 1f, 6f, 6f))
        advanceUntilIdle()

        val state = running(controller)
        assertEquals(TourAnchor.DetailsUnlink, state.spotlighted)
        assertEquals(Rect(1f, 1f, 6f, 6f), state.anchor)
        assertEquals(TourAnchor.DetailsUnlink, registry.wanted.value)
    }

    // ---- start()/preparing (issues #642, #641) ----

    @Test
    fun `start shows the welcome card with preparing true before awaitLibrary returns`() = runTest {
        val gate = CompletableDeferred<Boolean>()
        val controller = newController(pairId = 42, awaitLibrary = { gate.await() })

        controller.start()

        val state = running(controller)
        assertTrue(state.preparing)
        assertEquals(TOUR[0].id, state.step.id)
        assertNull(state.pairId)
    }

    @Test
    fun `next is ignored while preparing`() = runTest {
        val gate = CompletableDeferred<Boolean>()
        val controller = newController(pairId = 42, awaitLibrary = { gate.await() })
        controller.start()
        assertTrue(running(controller).preparing)

        controller.next()

        assertEquals(0, running(controller).index)
        assertTrue(running(controller).preparing)
    }

    @Test
    fun `preparing clears and the pair, total and willCleanUp update once the wait resolves`() = runTest {
        val gate = CompletableDeferred<Boolean>()
        val controller = newController(pairId = 42, awaitLibrary = { gate.await() })
        controller.start()
        assertTrue(running(controller).preparing)

        gate.complete(true)
        advanceUntilIdle()

        val state = running(controller)
        assertFalse(state.preparing)
        assertEquals(42, state.pairId)
        assertEquals(TOUR.size, state.total)
    }

    @Test
    fun `steps collapse to the skip card only once pick returns null after the wait`() = runTest {
        val gate = CompletableDeferred<Boolean>()
        val controller = newController(pairId = null, awaitLibrary = { gate.await() })
        controller.start()
        // Still on the full script's step 0 while preparing -- buildSteps hasn't run yet.
        assertEquals(TOUR.size, running(controller).total)

        gate.complete(true)
        advanceUntilIdle()

        val expectedTotal = TOUR.count { !it.needsPair } + 1
        assertEquals(expectedTotal, running(controller).total)
        assertNull(running(controller).pairId)
        assertFalse(running(controller).preparing)

        controller.advanceUntil(NO_PAIR_SKIP_STEP.id)
        assertEquals(NO_PAIR_SKIP_STEP.body, running(controller).step.body)
        controller.next()
        assertEquals("library_filters", running(controller).step.id)
    }

    @Test
    fun `a second start supersedes the first`() = runTest {
        val scope = unconfinedScope()
        val firstGate = CompletableDeferred<Boolean>()
        var calls = 0
        val controller = newController(
            pairId = 42,
            scope = scope,
            awaitLibrary = { if (++calls == 1) firstGate.await() else true },
        )

        controller.start() // suspends on firstGate
        assertTrue(running(controller).preparing)

        controller.start() // proceeds immediately (second call to awaitLibrary)
        advanceUntilIdle()

        assertFalse(running(controller).preparing)
        assertEquals(42, running(controller).pairId)

        // The stale first start() finally resumes; it must not stomp on the state
        // the second, superseding start() already settled.
        firstGate.complete(true)
        advanceUntilIdle()

        assertFalse(running(controller).preparing)
        assertEquals(42, running(controller).pairId)
        assertEquals(0, running(controller).index)
    }

    @Test
    fun `the first Library step opens the picked pair, later Library steps do not`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(pairId = 42, scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()

        controller.advanceUntil("library_open_pair")
        assertEquals(1, navLog.count { it == TourNav.OpenLibraryAt(42) })

        controller.advanceUntil("library_filters")
        assertEquals(1, navLog.count { it == TourNav.OpenLibraryAt(42) }) // still just the once
        assertTrue(navLog.contains(TourNav.GoToTab("library")))
    }

    @Test
    fun `entering Details, Reader or Player emits no navigation`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()

        controller.advanceUntil("details_chips")
        controller.advanceUntil("reader_tap_page")
        controller.advanceUntil("player_paused")

        assertTrue(navLog.none { it is TourNav.OpenDetails || it is TourNav.OpenReader })
    }

    @Test
    fun `leaving the reader block for Library emits PopToMain and GoToTab(library)`() = runTest {
        // The one automatic tab switch left (issue #597 follow-up): the reader
        // can be opened from any tab, but the script always resumes on Library
        // right after, regardless of which tab was active before it opened.
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_trick")
        navLog.clear()

        controller.next() // reader_trick -> library_filters

        assertEquals("library_filters", running(controller).step.id)
        assertTrue(navLog.contains(TourNav.PopToMain))
        assertTrue(navLog.contains(TourNav.GoToTab("library")))
    }

    @Test
    fun `entering a tap-the-tab step emits no further GoToTab beyond start's own`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        navLog.clear() // drop start()'s own GoToTab("home")

        controller.advanceUntil("home_tap_library")

        assertTrue(navLog.none { it is TourNav.GoToTab })
    }

    @Test
    fun `tapping Library advances the tour and opens the picked pair`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(pairId = 42, scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("home_tap_library")

        controller.onEvent(TourEvent.RouteShown("library"))

        assertEquals("library_open_pair", running(controller).step.id)
        assertTrue(navLog.contains(TourNav.OpenLibraryAt(42)))
    }

    @Test
    fun `RouteShown for a tab other than the one being waited on is ignored`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("home_tap_library")

        controller.onEvent(TourEvent.RouteShown("downloaded"))

        assertEquals("home_tap_library", running(controller).step.id)
    }

    @Test
    fun `a late duplicate RouteShown for the tab just tapped does not double-advance`() = runTest {
        // Compose can re-report a route (e.g. on recomposition); the step
        // waiting on it has already moved on by then, so a stray repeat must
        // not walk the tour forward again — matching is against the *current*
        // step's own expected event, not "was this route shown at some point."
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("home_tap_library")

        controller.onEvent(TourEvent.RouteShown("library"))
        assertEquals("library_open_pair", running(controller).step.id)

        controller.onEvent(TourEvent.RouteShown("library"))
        assertEquals("library_open_pair", running(controller).step.id)
    }

    @Test
    fun `quit from a reader step finishes, records completion, and pops to main`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_tap_page")
        navLog.clear()

        controller.quit()
        advanceUntilIdle()

        assertEquals(TourState.Finished, controller.state.value)
        assertTrue(navLog.contains(TourNav.PopToMain))
        coVerify { prefs.markCompleted() }
    }

    @Test
    fun `quit from Home finishes without popping to main`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()

        controller.quit()
        advanceUntilIdle()

        assertEquals(TourState.Finished, controller.state.value)
        assertFalse(navLog.contains(TourNav.PopToMain))
        coVerify { prefs.markCompleted() }
    }

    @Test
    fun `reaching the final step and pressing next finishes the tour and records it`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("done")

        controller.next()
        advanceUntilIdle()

        assertEquals(TourState.Finished, controller.state.value)
        coVerify { prefs.markCompleted() }
    }

    @Test
    fun `no qualifying pair collapses every needsPair step into one skip card`() = runTest {
        val controller = newController(pairId = null)
        controller.start()
        advanceUntilIdle()

        val expectedTotal = TOUR.count { !it.needsPair } + 1
        assertEquals(expectedTotal, running(controller).total)
        assertNull(running(controller).pairId)

        controller.advanceUntil(NO_PAIR_SKIP_STEP.id)
        assertEquals(NO_PAIR_SKIP_STEP.body, running(controller).step.body)

        controller.next()
        assertEquals("library_filters", running(controller).step.id)
    }

    // ---- cleanup on finish or quit (issue #597 tester feedback) ----

    @Test
    fun `an untouched pair emits CleanUp when the tour is quit`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(pairId = 42, scope = scope)
        coEvery { picker.isUntouched(42) } returns true
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        assertTrue(running(controller).willCleanUp)

        controller.quit()
        advanceUntilIdle()

        assertTrue(navLog.contains(TourNav.CleanUp(42)))
    }

    @Test
    fun `an untouched pair emits CleanUp when the final step finishes the tour`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(pairId = 42, scope = scope)
        coEvery { picker.isUntouched(42) } returns true
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("done")

        controller.next()
        advanceUntilIdle()

        assertEquals(TourState.Finished, controller.state.value)
        assertTrue(navLog.contains(TourNav.CleanUp(42)))
    }

    @Test
    fun `a touched pair never emits CleanUp`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(pairId = 42, scope = scope)
        coEvery { picker.isUntouched(42) } returns false
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        assertFalse(running(controller).willCleanUp)

        controller.quit()
        advanceUntilIdle()

        assertTrue(navLog.none { it is TourNav.CleanUp })
    }

    @Test
    fun `no pair at all never emits CleanUp`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(pairId = null, scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()

        controller.quit()
        advanceUntilIdle()

        assertTrue(navLog.none { it is TourNav.CleanUp })
    }

    @Test
    fun `adoptPair re-evaluates whether the tour will clean up`() = runTest {
        val controller = newController(pairId = 42)
        coEvery { picker.isUntouched(42) } returns true
        coEvery { picker.isUntouched(7) } returns false
        controller.start()
        advanceUntilIdle()
        assertTrue(running(controller).willCleanUp)

        controller.adoptPair(7)
        advanceUntilIdle()

        assertEquals(7, running(controller).pairId)
        assertFalse(running(controller).willCleanUp)
    }

    @Test
    fun `adoptPair can turn on cleanup for a pair the picker itself would not have`() = runTest {
        val controller = newController(pairId = 42)
        coEvery { picker.isUntouched(42) } returns false
        coEvery { picker.isUntouched(7) } returns true
        controller.start()
        advanceUntilIdle()
        assertFalse(running(controller).willCleanUp)

        controller.adoptPair(7)
        advanceUntilIdle()

        assertTrue(running(controller).willCleanUp)
    }

    // ---- loading vs. not reported (issue #652) ----

    @Test
    fun `a screen that says it is loading is never capped`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading
        // The reader parsing a full-length book took 41 s on the emulator -- past any cap
        // worth having -- and the card said the control was missing, then corrected itself.
        // A screen that has affirmatively said "still loading" is not a screen that went
        // quiet, and only the latter is what the cap exists for.
        registry.setSettled(TourScreen.Home, false)

        advanceTimeBy(120_000)
        runCurrent()
        assertEquals(AnchorResolution.Pending, running(controller).resolution)

        // ...and once it does settle, the ordinary 600 ms window applies.
        registry.setSettled(TourScreen.Home, true)
        advanceTimeBy(599)
        assertEquals(AnchorResolution.Pending, running(controller).resolution)
        advanceTimeBy(2)
        assertEquals(AnchorResolution.Missing, running(controller).resolution)
    }

    @Test
    fun `a screen that was loading and then left falls back to the cap`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()
        controller.next() // -> home_continue_reading
        registry.setSettled(TourScreen.Home, false)
        advanceTimeBy(5_000)
        runCurrent()
        registry.clearScreen(TourScreen.Home) // disposed without ever settling

        advanceTimeBy(29_999)
        assertEquals(AnchorResolution.Pending, running(controller).resolution)
        advanceTimeBy(2)
        assertEquals(AnchorResolution.Missing, running(controller).resolution)
    }

    @Test
    fun `start gives the first library load a full minute`() = runTest {
        // Pairs took 23 s to arrive for a library of a few hundred on the emulator; at the
        // old 20 s a tour accepted the instant the offer appeared timed out, picked from an
        // empty cache and collapsed its book section (issue #641 all over again).
        var askedFor = 0L
        val controller = newController(awaitLibrary = { timeoutMs -> askedFor = timeoutMs; true })
        controller.start()
        advanceUntilIdle()
        assertTrue("waited only $askedFor ms", askedFor >= 60_000)
    }
}
