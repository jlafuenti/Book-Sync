package com.booksync.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The uncaught-exception handler (issue #230).
 *
 * The contract that matters is *delegate, always*. The handler this replaces is
 * the Android runtime's `KillApplicationHandler`: it is what actually logs to
 * logcat, reports to Play Vitals and kills the process. Swallowing the
 * exception here would leave a frozen app that never shows the "Tandem keeps
 * stopping" dialog and never reaches Vitals — trading the aggregate crash data
 * we do have for the manual report we might get.
 *
 * Writing the report must therefore never be able to stop the delegation, which
 * is why the sink is a plain lambda and its failures are swallowed.
 */
class CrashLogHandlerTest {

    private val ctx = CrashContext(
        versionName = "1.4.0",
        versionCode = 42,
        deviceManufacturer = "Google",
        deviceModel = "Pixel 7",
        androidRelease = "14",
        androidSdkInt = 34,
    )

    private class RecordingHandler : Thread.UncaughtExceptionHandler {
        var thread: Thread? = null
        var throwable: Throwable? = null
        override fun uncaughtException(t: Thread, e: Throwable) {
            thread = t
            throwable = e
        }
    }

    @Test
    fun `the crash is appended to the diagnostics log`() {
        val written = StringBuilder()
        val handler = CrashLogHandler(
            previous = RecordingHandler(),
            context = ctx,
            appendToLog = { written.append(it) },
            nowIso = { "2026-09-03T08:15:00Z" },
        )

        handler.uncaughtException(Thread.currentThread(), IllegalStateException("boom"))

        val text = written.toString()
        assertTrue(text.contains("java.lang.IllegalStateException: boom"))
        assertTrue(text.contains("App: Tandem 1.4.0 (42)"))
        assertTrue(text.contains("2026-09-03T08:15:00Z"))
    }

    @Test
    fun `the previous handler still gets the crash`() {
        val previous = RecordingHandler()
        val handler = CrashLogHandler(previous, ctx, appendToLog = {}, nowIso = { "t" })
        val thrown = IllegalStateException("boom")

        handler.uncaughtException(Thread.currentThread(), thrown)

        assertSame(thrown, previous.throwable)
        assertSame(Thread.currentThread(), previous.thread)
    }

    @Test
    fun `a failing log write does not swallow the crash`() {
        // filesDir full, storage revoked, a SecurityException from a background
        // thread — whatever the reason, the process must still die the way
        // Android expects it to.
        val previous = RecordingHandler()
        val handler = CrashLogHandler(
            previous,
            ctx,
            appendToLog = { throw java.io.IOException("no space left on device") },
            nowIso = { "t" },
        )

        handler.uncaughtException(Thread.currentThread(), IllegalStateException("boom"))

        assertNotNull("the previous handler was never reached", previous.throwable)
        assertEquals("boom", previous.throwable?.message)
    }

    @Test
    fun `no previous handler is not a crash in the crash handler`() {
        // Thread.getDefaultUncaughtExceptionHandler() is null in a plain JVM and
        // could be null on some hosts; a NullPointerException raised while
        // handling a crash is the worst possible failure mode.
        val handler = CrashLogHandler(previous = null, context = ctx, appendToLog = {}, nowIso = { "t" })

        handler.uncaughtException(Thread.currentThread(), IllegalStateException("boom"))
    }

    @Test
    fun `install chains onto whatever handler was already registered`() {
        val original = Thread.getDefaultUncaughtExceptionHandler()
        val previous = RecordingHandler()
        try {
            Thread.setDefaultUncaughtExceptionHandler(previous)

            val installed = CrashLogHandler.install(ctx, appendToLog = {})

            assertSame(installed, Thread.getDefaultUncaughtExceptionHandler())
            installed.uncaughtException(Thread.currentThread(), IllegalStateException("boom"))
            assertEquals("boom", previous.throwable?.message)
        } finally {
            Thread.setDefaultUncaughtExceptionHandler(original)
        }
    }

    @Test
    fun `installing twice does not chain the handler onto itself`() {
        // BookSyncApp.onCreate can run more than once per process (Android Auto
        // starts the media service in the same process); a self-chain is an
        // infinite recursion inside the crash path.
        val original = Thread.getDefaultUncaughtExceptionHandler()
        try {
            val first = CrashLogHandler.install(ctx, appendToLog = {})
            val second = CrashLogHandler.install(ctx, appendToLog = {})

            assertSame(first, second)
            assertSame(first, Thread.getDefaultUncaughtExceptionHandler())
        } finally {
            Thread.setDefaultUncaughtExceptionHandler(original)
        }
    }
}
