package com.booksync.data.repository

import com.booksync.di.ApplicationScope
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** `@Named` key for the injected "is a session signed in" flow — see [SIGNED_IN_FLOW_QUALIFIER] below. */
const val SIGNED_IN_FLOW_QUALIFIER = "librarySignedIn"

/**
 * Where a sign-in's library fetch stands.
 *
 * Forms a chain that only ever moves forward within one sign-in:
 * `Idle -> Loading -> PairsLoaded -> Loaded`. `Failed` is reachable only while
 * pairs have not yet loaded this sign-in — see [LibraryLoader] for why.
 */
enum class LibraryLoadState { Idle, Loading, PairsLoaded, Loaded, Failed }

/**
 * Owns the one startup fetch of the server library (issue #641).
 *
 * Before this, `refreshPairs/refreshEbooks/refreshAudiobooks` were only ever
 * called from `LibraryViewModel.init`, so a fresh sign-in landed on Home
 * reading an empty Room cache with no fetch in flight until the user opened
 * the Library tab — Home's `LOADING` state only meant "Room has not answered
 * yet" (issue #624), not "a fetch is running". This class is the single place
 * that now runs and tracks that fetch, so both `HomeViewModel` and
 * `LibraryViewModel` observe (or wait on) the same run instead of each
 * kicking off their own.
 *
 * [state] is deliberately monotonic within a sign-in: once pairs have loaded,
 * a later transient failure (a dropped connection on pull-to-refresh, say)
 * must not walk it backwards, or a screen reading [state] would regress from
 * "has books" back to a loading/failed view for data it already has cached.
 * [refreshing] is the separate signal for "a run is in flight right now" —
 * callers that only want a spinner read that instead of inferring it from
 * [state].
 */
@Singleton
class LibraryLoader @Inject constructor(
    private val repository: BookSyncRepository,
    @ApplicationScope private val scope: CoroutineScope,
    @Named(SIGNED_IN_FLOW_QUALIFIER) signedIn: Flow<Boolean>,
) {
    private val _state = MutableStateFlow(LibraryLoadState.Idle)
    val state: StateFlow<LibraryLoadState> = _state.asStateFlow()

    /** The most recent refresh failure, so [com.booksync.ui.library.LibraryViewModel]
     *  can keep producing the messages it always has. Cleared on sign-out. */
    private val _lastError = MutableStateFlow<Throwable?>(null)
    val lastError: StateFlow<Throwable?> = _lastError.asStateFlow()

    /** True while a run is in flight — distinct from [state], which never regresses. */
    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()

    private val jobLock = Any()
    private var currentJob: Job? = null

    init {
        // Resets for the next sign-in rather than leaving the previous
        // account's Loaded/Failed answer sitting there — nothing else clears
        // this. Reusing TokenManager's existing token flow rather than a new
        // persistence mechanism; see the SIGNED_IN_FLOW_QUALIFIER binding in
        // AppModule.
        scope.launch {
            signedIn.distinctUntilChanged().collect { isSignedIn ->
                if (!isSignedIn) {
                    synchronized(jobLock) {
                        currentJob?.cancel()
                        currentJob = null
                    }
                    _state.value = LibraryLoadState.Idle
                    _lastError.value = null
                }
            }
        }
    }

    /**
     * Runs (or joins) the fetch. Single-flighted: a caller that arrives while a
     * run is already in flight gets that same run's [Job] rather than starting
     * a second one that would duplicate the network calls.
     */
    fun refresh(): Job {
        synchronized(jobLock) {
            currentJob?.let { existing -> if (existing.isActive) return existing }
            val job = scope.launch { runRefresh() }
            currentJob = job
            return job
        }
    }

    private suspend fun runRefresh() {
        _refreshing.value = true
        // Cleared per-run, not just on sign-out: LibraryViewModel.refresh reads
        // lastError right after join() to decide what message to show, and a
        // stale error from a run two attempts ago must not outlive its own run.
        _lastError.value = null
        try {
            // Idle/Failed both mean "pairs have not loaded this sign-in yet" —
            // anything past that (PairsLoaded/Loaded) must not be walked back
            // to Loading by a later refresh.
            if (_state.value == LibraryLoadState.Idle || _state.value == LibraryLoadState.Failed) {
                // Cancellation is cooperative and only checked at a suspension
                // point: a run cancelled (by sign-out) while it is executing
                // plain, non-suspending code between two suspending calls would
                // not otherwise notice until its next `repository.*` call, and
                // could write a stale non-Idle state for the next account in
                // the meantime. ensureActive() immediately ahead of every
                // `_state` write closes that gap — see LibraryLoaderTest's
                // "cancelled by sign-out" test.
                currentCoroutineContext().ensureActive()
                _state.value = LibraryLoadState.Loading
            }
            repository.refreshPairs()
            if (_state.value != LibraryLoadState.Loaded) {
                currentCoroutineContext().ensureActive()
                _state.value = LibraryLoadState.PairsLoaded
            }
            repository.refreshEbooks()
            repository.refreshAudiobooks()
            currentCoroutineContext().ensureActive()
            _state.value = LibraryLoadState.Loaded
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            _lastError.value = e
            // Once pairs have loaded this sign-in, a failure here is "the rest
            // of the refresh didn't finish" — not "back to square one".
            if (_state.value != LibraryLoadState.PairsLoaded && _state.value != LibraryLoadState.Loaded) {
                currentCoroutineContext().ensureActive()
                _state.value = LibraryLoadState.Failed
            }
        } finally {
            _refreshing.value = false
        }
    }

    /**
     * Waits (up to [timeoutMs]) for pairs to be available, starting a run if
     * none is in flight yet. Returns as soon as [state] reaches [LibraryLoadState.PairsLoaded]
     * — it does not wait for ebooks/audiobooks too, since callers that only
     * need pairs (e.g. Home's carousels) would otherwise wait longer than they
     * have to.
     */
    suspend fun awaitPairs(timeoutMs: Long): Boolean {
        if (_state.value == LibraryLoadState.Idle) refresh()
        if (_state.value == LibraryLoadState.PairsLoaded || _state.value == LibraryLoadState.Loaded) return true
        if (_state.value == LibraryLoadState.Failed) return false

        val reached = withTimeoutOrNull(timeoutMs) {
            state.first {
                it == LibraryLoadState.PairsLoaded ||
                    it == LibraryLoadState.Loaded ||
                    it == LibraryLoadState.Failed
            }
        }
        return reached == LibraryLoadState.PairsLoaded || reached == LibraryLoadState.Loaded
    }
}
