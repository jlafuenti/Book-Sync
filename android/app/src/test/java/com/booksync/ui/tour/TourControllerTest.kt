package com.booksync.ui.tour

import androidx.compose.ui.geometry.Rect
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The walkthrough's state machine (issue #597 §3): start picks a pair and
 * enters step 0; `next`/`back` walk the script; a `TapAnchor` step advances
 * only on its expected event and ignores `next()`; a step whose anchor never
 * shows up degrades after the timeout; a library with no synced pair
 * collapses every `needsPair` step into one skip card; quitting — or the
 * final `next()` — finishes the tour and records it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TourControllerTest {

    private val prefs = mockk<TourPrefs>(relaxed = true)
    private val picker = mockk<TourPairPicker>()
    private lateinit var registry: TourAnchorRegistry

    /**
     * Unconfined, but sharing the [TestScope]'s scheduler: launched bodies run
     * eagerly (so `state.value` is current the moment a call returns) while
     * `delay()`-based anchor timeouts still obey `advanceTimeBy` /
     * `advanceUntilIdle` rather than real wall-clock time.
     *
     * Tests that assert on [TourController.nav] must collect it through this
     * *same* scope (not `backgroundScope`, which runs on the `TestScope`'s own
     * `StandardTestDispatcher`) — otherwise the collector is merely scheduled,
     * not yet attached, by the time an eagerly-run `start()`/`next()` emits.
     */
    private fun TestScope.unconfinedScope(): CoroutineScope =
        CoroutineScope(UnconfinedTestDispatcher(testScheduler))

    private fun TestScope.newController(pairId: Int? = 42, scope: CoroutineScope = unconfinedScope()): TourController {
        registry = TourAnchorRegistry()
        coEvery { picker.pick() } returns pairId
        return TourController(registry = registry, prefs = prefs, picker = picker, scope = scope)
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
    fun `start picks a pair and enters step 0, needing no nav since Home is already showing`() = runTest {
        // Issue #597 follow-up: the tour used to switch to the Home tab itself
        // on start, but Home is where the tour is offered in the first place —
        // there is nothing to navigate to, guided or otherwise.
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
        assertTrue(navLog.isEmpty())
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
    fun `the reader tap-page step shows the bars itself after the grace period`() = runTest {
        // A user who never taps the page would be stuck; after the grace period
        // the tour asks the reader to raise its bars, whose ReaderBarsShown then
        // advances the step as if the user had tapped.
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()
        controller.advanceUntil("reader_tap_page")

        advanceTimeBy(READER_BARS_GRACE_MS - 1)
        assertFalse(navLog.contains(TourNav.ShowReaderBars))

        advanceTimeBy(2)
        assertTrue(navLog.contains(TourNav.ShowReaderBars))

        // Leaving the step first cancels the request.
        val controller2 = newController(scope = scope)
        val navLog2 = mutableListOf<TourNav>()
        scope.launch { controller2.nav.collect { navLog2.add(it) } }
        controller2.start()
        advanceUntilIdle()
        controller2.advanceUntil("reader_tap_page")
        controller2.onEvent(TourEvent.ReaderBarsShown)
        advanceTimeBy(READER_BARS_GRACE_MS + 10)
        assertFalse(navLog2.contains(TourNav.ShowReaderBars))
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

    @Test
    fun `an anchor rect already present when the step is entered is not degraded`() = runTest {
        val controller = newController()
        registry.set(TourAnchor.HomeContinueReading, Rect(0f, 0f, 10f, 10f))
        controller.start()
        advanceUntilIdle()

        controller.next() // -> home_continue_reading

        val state = running(controller)
        assertFalse(state.degraded)
        assertEquals(Rect(0f, 0f, 10f, 10f), state.anchor)
    }

    @Test
    fun `no anchor rect within the timeout renders the step degraded`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()

        controller.next() // -> home_continue_reading, no rect ever registered
        assertFalse(running(controller).degraded) // not yet — still waiting

        advanceTimeBy(1_600)
        advanceUntilIdle()

        assertTrue(running(controller).degraded)
        assertNull(running(controller).anchor)
    }

    @Test
    fun `an anchor rect that arrives before the timeout clears the degrade`() = runTest {
        val controller = newController()
        controller.start()
        advanceUntilIdle()

        controller.next() // -> home_continue_reading
        advanceTimeBy(500)
        registry.set(TourAnchor.HomeContinueReading, Rect(1f, 1f, 2f, 2f))
        advanceUntilIdle()

        val state = running(controller)
        assertFalse(state.degraded)
        assertEquals(Rect(1f, 1f, 2f, 2f), state.anchor)
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
    fun `entering a tap-the-tab step emits no GoToTab — the user's own tap does that`() = runTest {
        val scope = unconfinedScope()
        val controller = newController(scope = scope)
        val navLog = mutableListOf<TourNav>()
        scope.launch { controller.nav.collect { navLog.add(it) } }
        controller.start()
        advanceUntilIdle()

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
}
