package com.booksync.data.remote

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking

/**
 * A value loaded once, off the main thread, but readable synchronously (#318).
 *
 * Four singletons used to do `init { runBlocking { dataStore.data.first() } }`.
 * Hilt builds them inside `Hilt_BookSyncApp.onCreate()`, so that was a disk read
 * on the main thread before the first frame — a StrictMode `diskRead` violation
 * and a real cost on a slow device.
 *
 * **Why a `Deferred` and not a flag plus a fallback.** The obvious shape — seed in
 * the background, and if a reader arrives first let it do its own blocking read —
 * runs the loader twice. Three of the four callers only read, so that would merely
 * be wasteful. `DeviceIdManager` is the fourth: when no device id is stored it
 * *generates and persists a UUID*. Two racing loads would mint two ids, persist one
 * and cache the other, and that id keys `position_hints` and decides which device a
 * reading position came from. It would only misfire on a first launch, on a device
 * fast enough to lose the race — close to undiagnosable afterwards.
 *
 * `async` memoises: every waiter awaits the same computation and the body runs
 * exactly once. The single-flight is the design, not a refinement of it.
 *
 * Reads before the seed lands **block** rather than returning a default. That is
 * deliberate: after #228 `BaseUrlInterceptor` reads the server URL on every
 * request, and a request sent to the placeholder host — or with no bearer — is a
 * worse outcome than waiting a few milliseconds for the value that was already
 * being fetched.
 */
class SeededValue<T>(
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
    private val load: suspend () -> T,
) {

    /** Distinguishes "cached, and the value is null" from "not cached yet". */
    private class Holder<T>(val value: T)

    private val lock = Any()

    @Volatile
    private var holder: Holder<T>? = null

    private var seed: Deferred<T> = newSeed()

    init {
        // Warm it. runCatching so a failed load cannot take the scope down with
        // it; get() re-raises to whoever actually asked.
        scope.launch {
            runCatching { seed.await() }.onSuccess { holder = Holder(it) }
        }
    }

    private fun newSeed(): Deferred<T> = scope.async { load() }

    /**
     * The value, waiting for the in-flight load if it has not finished.
     *
     * @throws Throwable whatever [load] threw. The seed is reset first, so a
     *   transient failure does not leave the app permanently unable to read its
     *   own tokens — the next call starts a fresh attempt.
     */
    fun get(): T {
        holder?.let { return it.value }

        val current = synchronized(lock) { seed }
        return try {
            runBlocking { current.await() }.also { holder = Holder(it) }
        } catch (t: Throwable) {
            synchronized(lock) { if (seed === current) seed = newSeed() }
            throw t
        }
    }
}
