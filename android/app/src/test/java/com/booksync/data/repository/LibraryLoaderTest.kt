package com.booksync.data.repository

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import java.io.IOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #641: the only startup caller of `refreshPairs/refreshEbooks/refreshAudiobooks`
 * was `LibraryViewModel.init`, so a fresh sign-in landed on Home with an empty Room
 * cache and no fetch in flight until the user opened the Library tab. [LibraryLoader]
 * is the single place that now owns that fetch, so both Home and Library can await
 * (or observe) the same run instead of each kicking off their own.
 *
 * [LibraryLoader.state] only ever moves forward within a sign-in
 * (Idle -> Loading -> PairsLoaded -> Loaded); `Failed` is reachable only while pairs
 * have not yet loaded this sign-in, and a later refresh's failure leaves state exactly
 * where it was and simply updates [LibraryLoader.lastError] — otherwise Home would
 * regress from HAS_BOOKS back to a loading/empty state on a transient refresh error.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibraryLoaderTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)

    /**
     * Unconfined but sharing the [TestScope]'s scheduler, same rationale as
     * `TourControllerTest.unconfinedScope`: launched bodies run eagerly so
     * `state.value` is current the instant a call returns, while `withTimeoutOrNull`
     * still obeys virtual time rather than the real wall clock.
     */
    private fun TestScope.newLoader(signedIn: MutableStateFlow<Boolean> = MutableStateFlow(true)): LibraryLoader {
        val scope = CoroutineScope(UnconfinedTestDispatcher(testScheduler))
        return LibraryLoader(repository = repository, scope = scope, signedIn = signedIn)
    }

    @Test
    fun `refresh moves through Loading then PairsLoaded then Loaded in order`() = runTest {
        val pairsGate = CompletableDeferred<Unit>()
        val ebooksGate = CompletableDeferred<Unit>()
        coEvery { repository.refreshPairs() } coAnswers { pairsGate.await() }
        coEvery { repository.refreshEbooks() } coAnswers { ebooksGate.await() }
        coEvery { repository.refreshAudiobooks() } returns Unit

        val loader = newLoader()
        assertEquals(LibraryLoadState.Idle, loader.state.value)

        val job = loader.refresh()
        assertEquals(LibraryLoadState.Loading, loader.state.value)

        pairsGate.complete(Unit)
        advanceUntilIdle()
        assertEquals(LibraryLoadState.PairsLoaded, loader.state.value)

        ebooksGate.complete(Unit)
        job.join()
        assertEquals(LibraryLoadState.Loaded, loader.state.value)
    }

    @Test
    fun `two refresh calls during one run return the same Job and fetch only once`() = runTest {
        val pairsGate = CompletableDeferred<Unit>()
        coEvery { repository.refreshPairs() } coAnswers { pairsGate.await() }
        coEvery { repository.refreshEbooks() } returns Unit
        coEvery { repository.refreshAudiobooks() } returns Unit

        val loader = newLoader()
        val job1 = loader.refresh()
        val job2 = loader.refresh()
        assertSame(job1, job2)

        pairsGate.complete(Unit)
        job1.join()

        coVerify(exactly = 1) { repository.refreshPairs() }
    }

    @Test
    fun `a failure before pairs load sets Failed and lastError without throwing`() = runTest {
        val error = IOException("boom")
        coEvery { repository.refreshPairs() } throws error

        val loader = newLoader()
        loader.refresh().join()

        assertEquals(LibraryLoadState.Failed, loader.state.value)
        assertSame(error, loader.lastError.value)
    }

    @Test
    fun `a failure on a later refresh leaves state at Loaded and only updates lastError`() = runTest {
        coEvery { repository.refreshPairs() } returns Unit
        coEvery { repository.refreshEbooks() } returns Unit
        coEvery { repository.refreshAudiobooks() } returns Unit

        val loader = newLoader()
        loader.refresh().join()
        assertEquals(LibraryLoadState.Loaded, loader.state.value)
        assertNull(loader.lastError.value)

        val error = IOException("later boom")
        coEvery { repository.refreshPairs() } throws error
        loader.refresh().join()

        assertEquals(LibraryLoadState.Loaded, loader.state.value)
        assertSame(error, loader.lastError.value)
    }

    @Test
    fun `a successful refresh after a previous failure clears lastError`() = runTest {
        coEvery { repository.refreshPairs() } throws IOException("first boom")

        val loader = newLoader()
        loader.refresh().join()
        assertEquals(LibraryLoadState.Failed, loader.state.value)
        assertTrue(loader.lastError.value != null)

        coEvery { repository.refreshPairs() } returns Unit
        coEvery { repository.refreshEbooks() } returns Unit
        coEvery { repository.refreshAudiobooks() } returns Unit
        loader.refresh().join()

        assertEquals(LibraryLoadState.Loaded, loader.state.value)
        assertNull(loader.lastError.value)
    }

    @Test
    fun `signing out resets state to Idle and clears lastError`() = runTest {
        coEvery { repository.refreshPairs() } throws IOException("boom")

        val signedIn = MutableStateFlow(true)
        val loader = newLoader(signedIn)
        loader.refresh().join()
        assertEquals(LibraryLoadState.Failed, loader.state.value)
        assertTrue(loader.lastError.value != null)

        signedIn.value = false
        advanceUntilIdle()

        assertEquals(LibraryLoadState.Idle, loader.state.value)
        assertNull(loader.lastError.value)
    }

    /**
     * Cancellation is cooperative: `currentJob?.cancel()` marks the run
     * cancelled, but a coroutine only notices at its next *cancellable*
     * suspension point. If a write to [LibraryLoader.state] sits between where
     * cancellation was requested and the next such check, it can execute
     * anyway, leaving a stale non-`Idle` state for the next account — exactly
     * the state [com.booksync.ui.home.HomeViewModel]'s `init` would not retry
     * from (issue #641 review).
     *
     * `refreshPairs` awaits its gate inside `withContext(NonCancellable)` —
     * standing in for a call that does not itself check for cancellation
     * (e.g. a blocking network call) — so cancelling the run does not
     * interrupt it there the way an ordinary cancellable suspension would.
     * Completing the gate only after sign-out has been signalled lets the
     * coroutine actually resume past a request it has no way to have noticed
     * yet, reaching the line that would write `PairsLoaded`: the fix's
     * `ensureActive()` immediately ahead of that write is what stops it.
     */
    @Test
    fun `a run cancelled by sign-out never writes state after the cancellation`() = runTest {
        val pairsGate = CompletableDeferred<Unit>()
        coEvery { repository.refreshPairs() } coAnswers {
            withContext(NonCancellable) { pairsGate.await() }
        }

        val signedIn = MutableStateFlow(true)
        val loader = newLoader(signedIn)
        val job = loader.refresh()
        assertEquals(LibraryLoadState.Loading, loader.state.value)

        signedIn.value = false
        assertEquals(LibraryLoadState.Idle, loader.state.value)

        // Only now does the gated call get to resume — after cancellation was
        // already requested, and via a suspension that would not have noticed it.
        pairsGate.complete(Unit)
        job.join()

        assertEquals(LibraryLoadState.Idle, loader.state.value)
    }

    @Test
    fun `awaitPairs returns true at PairsLoaded without waiting for Loaded`() = runTest {
        val ebooksGate = CompletableDeferred<Unit>()
        coEvery { repository.refreshPairs() } returns Unit
        coEvery { repository.refreshEbooks() } coAnswers { ebooksGate.await() }
        coEvery { repository.refreshAudiobooks() } returns Unit

        val loader = newLoader()
        val result = loader.awaitPairs(timeoutMs = 5_000)

        assertTrue(result)
        assertEquals(LibraryLoadState.PairsLoaded, loader.state.value)
    }

    @Test
    fun `awaitPairs returns false on timeout`() = runTest {
        val pairsGate = CompletableDeferred<Unit>()
        coEvery { repository.refreshPairs() } coAnswers { pairsGate.await() }

        val loader = newLoader()
        val result = loader.awaitPairs(timeoutMs = 1_000)

        assertFalse(result)
    }

    @Test
    fun `awaitPairs starts a refresh when the loader is Idle`() = runTest {
        coEvery { repository.refreshPairs() } returns Unit
        coEvery { repository.refreshEbooks() } returns Unit
        coEvery { repository.refreshAudiobooks() } returns Unit

        val loader = newLoader()
        assertEquals(LibraryLoadState.Idle, loader.state.value)

        loader.awaitPairs(timeoutMs = 5_000)

        coVerify(exactly = 1) { repository.refreshPairs() }
    }

    // ---- reading positions (issue #652) ----

    @Test
    fun `a successful load drains pending writes and pulls positions for the loaded pairs`() = runTest {
        // Both used to run only from LibraryViewModel.refresh, so after a fresh sign-in Home
        // had no Continue Reading until the Library tab was opened.
        val pairs = listOf(mockk<com.booksync.data.local.entity.BookPairEntity>(relaxed = true))
        coEvery { repository.getPairsFlow() } returns kotlinx.coroutines.flow.flowOf(pairs)

        val loader = newLoader()
        loader.refresh().join()
        advanceUntilIdle()

        io.mockk.coVerifyOrder {
            repository.processPendingSync()
            repository.syncAllBookmarksAndProgress(pairs)
        }
    }

    @Test
    fun `a failing position sync does not turn a loaded library into a failed one`() = runTest {
        coEvery { repository.getPairsFlow() } returns kotlinx.coroutines.flow.flowOf(emptyList())
        coEvery { repository.syncAllBookmarksAndProgress(any()) } throws java.io.IOException("offline")

        val loader = newLoader()
        loader.refresh().join()
        advanceUntilIdle()

        assertEquals(LibraryLoadState.Loaded, loader.state.value)
        assertEquals(null, loader.lastError.value)
    }

    @Test
    fun `a failed load does not start the position sync`() = runTest {
        coEvery { repository.refreshPairs() } throws java.io.IOException("offline")

        val loader = newLoader()
        loader.refresh().join()
        advanceUntilIdle()

        coVerify(exactly = 0) { repository.syncAllBookmarksAndProgress(any()) }
    }
}
