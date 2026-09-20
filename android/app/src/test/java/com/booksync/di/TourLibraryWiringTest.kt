package com.booksync.di

import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LibraryLoader
import com.booksync.ui.tour.TourAnchorRegistry
import com.booksync.ui.tour.TourPairPicker
import com.booksync.ui.tour.TourPrefs
import com.booksync.ui.tour.TourState
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The production [com.booksync.ui.tour.TourController] must wait for the library's pairs
 * before it chooses a book (issue #641). `TourController`'s own tests inject `awaitLibrary`
 * directly, so they pass whether or not [AppModule.provideTourController] ever wires it —
 * and unwired, a walkthrough accepted right after a fresh sign-in picks from an empty Room
 * cache and collapses its whole book section into the "Skipped" card. This pins the wiring.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TourLibraryWiringTest {

    @Test
    fun `the provided controller does not pick a pair until the loader has pairs`() = runTest {
        val scope = CoroutineScope(UnconfinedTestDispatcher(testScheduler))
        val pairsGate = CompletableDeferred<Unit>()
        val repository = mockk<BookSyncRepository>(relaxed = true)
        coEvery { repository.refreshPairs() } coAnswers { pairsGate.await() }
        val loader = LibraryLoader(repository = repository, scope = scope, signedIn = MutableStateFlow(true))
        val picker = mockk<TourPairPicker>()
        coEvery { picker.pick() } returns 42
        coEvery { picker.isUntouched(any()) } returns false

        val controller = AppModule.provideTourController(
            registry = TourAnchorRegistry(),
            prefs = mockk<TourPrefs>(relaxed = true),
            picker = picker,
            scope = scope,
            loader = loader,
        )

        controller.start()
        runCurrent()

        // The welcome card is up, a fetch is in flight, and nothing has been picked yet.
        assertTrue((controller.state.value as TourState.Running).preparing)
        coVerify(exactly = 1) { repository.refreshPairs() }
        coVerify(exactly = 0) { picker.pick() }

        pairsGate.complete(Unit)
        runCurrent()

        val running = controller.state.value as TourState.Running
        assertFalse(running.preparing)
        assertEquals(42, running.pairId)
    }
}
