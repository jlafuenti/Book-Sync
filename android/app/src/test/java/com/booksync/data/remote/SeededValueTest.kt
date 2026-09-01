package com.booksync.data.remote

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * One-shot value that is seeded off the main thread but read synchronously
 * (issue #318).
 *
 * Four singletons used to do `init { runBlocking { dataStore… } }`, and Hilt
 * builds them inside `Hilt_BookSyncApp.onCreate()` — so the first read of
 * `booksync_prefs` was a disk read on the main thread before the first frame.
 *
 * The obvious fix — seed in the background, fall back to a blocking read if
 * someone asks first — is **wrong here**, and that is what this class exists to
 * prevent. `DeviceIdManager`'s seed does not merely read: when no device id is
 * stored it *generates and persists a UUID*. Two racing loads would mint two
 * ids, persist one and cache the other. That id keys `position_hints` and
 * decides which device a reading position came from.
 *
 * So the seed is a `Deferred`, which memoises by construction: every waiter
 * awaits the same computation and the body runs exactly once. The single-flight
 * is the whole point, not an optimisation.
 */
class SeededValueTest {

    private fun scope() = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Test
    fun `the loader runs exactly once even when readers race the warm-up`() {
        val calls = AtomicInteger(0)
        val released = CountDownLatch(1)

        val value = SeededValue(scope()) {
            calls.incrementAndGet()
            // Hold the seed open so the readers below are genuinely concurrent
            // with it — the first-launch window this is all about.
            released.await()
            "loaded"
        }

        val ready = CountDownLatch(4)
        val go = CountDownLatch(1)
        val results = java.util.Collections.synchronizedList(mutableListOf<String>())
        val readers = (1..4).map {
            Thread {
                ready.countDown()
                go.await()
                results += value.get()
            }
        }
        readers.forEach { it.start() }
        ready.await()
        go.countDown()

        released.countDown()
        readers.forEach { it.join(10_000) }

        assertEquals("the loader must run once, not once per caller", 1, calls.get())
        assertEquals(listOf("loaded", "loaded", "loaded", "loaded"), results)
    }

    @Test
    fun `a read before the seed finishes waits for the real value`() {
        // Never a default or a null: a request that went out with the wrong
        // server URL or no token would be worse than a few ms of waiting.
        val value = SeededValue(scope()) {
            delay(150)
            "real"
        }

        assertEquals("real", value.get())
    }

    @Test
    fun `later reads are served from the cache`() {
        val calls = AtomicInteger(0)
        val value = SeededValue(scope()) {
            calls.incrementAndGet()
            "x"
        }

        repeat(50) { assertEquals("x", value.get()) }

        assertEquals(1, calls.get())
    }

    @Test
    fun `construction does not block on the loader`() {
        // The reason this class exists: the constructor runs on the main thread
        // inside Application.onCreate.
        //
        // Warm the dispatcher first. Without this the measurement includes the
        // JVM's first Dispatchers.IO pool spin-up, which made this fail
        // intermittently depending on which test ran first — a flake in the test,
        // not the code.
        SeededValue(scope()) { "warm" }.get()

        val started = System.nanoTime()
        SeededValue(scope()) {
            delay(5_000)
            "slow"
        }
        val elapsedMs = (System.nanoTime() - started) / 1_000_000

        assertTrue(
            "constructing must not wait for the load (took ${elapsedMs}ms)",
            elapsedMs < 1_000,
        )
    }

    @Test
    fun `a loader that throws is retried rather than caching the failure`() {
        // A transient DataStore failure must not leave the app permanently
        // unable to read its own tokens.
        val calls = AtomicInteger(0)
        val value = SeededValue(scope()) {
            if (calls.incrementAndGet() == 1) error("transient") else "recovered"
        }

        // First get() surfaces the failure...
        runCatching { value.get() }
        // ...and the next one tries again instead of rethrowing forever.
        assertEquals("recovered", value.get())
    }

    @Test
    fun `nullable values are cached, not reloaded`() {
        // Tokens are legitimately null when signed out. A naive `cached ?: load()`
        // would re-read the disk on every request for a signed-out user.
        val calls = AtomicInteger(0)
        val value = SeededValue<String?>(scope()) {
            calls.incrementAndGet()
            null
        }

        repeat(10) { assertEquals(null, value.get()) }

        assertEquals("null is a real answer, not a cache miss", 1, calls.get())
    }
}
