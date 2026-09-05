package com.booksync.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What a crash report has to say (issue #230).
 *
 * There is no crash SDK and there will not be one before launch, so the only
 * report the owner will ever see is the one the user shares by hand. That makes
 * the header load-bearing: a stack trace from an R8-minified release build is
 * close to useless without the version it came from (`mapping.txt` is per
 * release) and the device/OS it happened on.
 */
class CrashReportFormatterTest {

    private val ctx = CrashContext(
        versionName = "1.4.0",
        versionCode = 42,
        deviceManufacturer = "Google",
        deviceModel = "Pixel 7",
        androidRelease = "14",
        androidSdkInt = 34,
    )

    private fun boom(): Throwable = try {
        throw IllegalStateException("sync map version 3 is not 4")
    } catch (e: IllegalStateException) {
        e
    }

    @Test
    fun `the header names the version, the device and the OS`() {
        val report = CrashReportFormatter.format(
            throwable = boom(),
            context = ctx,
            threadName = "main",
            timestampIso = "2026-09-03T08:15:00Z",
        )

        assertTrue(report.contains("App: Tandem 1.4.0 (42)"))
        assertTrue(report.contains("Device: Google Pixel 7"))
        assertTrue(report.contains("Android: 14 (SDK 34)"))
        assertTrue(report.contains("Thread: main"))
        assertTrue(report.contains("Time: 2026-09-03T08:15:00Z"))
    }

    @Test
    fun `the full stack trace is included, not just the message`() {
        val report = CrashReportFormatter.format(boom(), ctx, "main", "2026-09-03T08:15:00Z")

        assertTrue(report.contains("java.lang.IllegalStateException: sync map version 3 is not 4"))
        // The frame that threw. Without frames the report cannot be matched to a
        // line of code, which is the entire point of collecting it.
        assertTrue(report.contains("CrashReportFormatterTest"))
        assertTrue(report.contains("\tat "))
    }

    @Test
    fun `the cause chain survives`() {
        // Coroutine and Retrofit failures almost always arrive wrapped; dropping
        // the cause throws away the only frame that names the real fault.
        val wrapped = RuntimeException("could not refresh library", boom())

        val report = CrashReportFormatter.format(wrapped, ctx, "DefaultDispatcher-worker-1", "t")

        assertTrue(report.contains("could not refresh library"))
        assertTrue(report.contains("Caused by: java.lang.IllegalStateException"))
    }

    @Test
    fun `the report is delimited so it can be found in a long log`() {
        val report = CrashReportFormatter.format(boom(), ctx, "main", "t")

        assertTrue(report.startsWith(CrashReportFormatter.START_MARKER))
        assertTrue(report.trimEnd().endsWith(CrashReportFormatter.END_MARKER))
        assertTrue("the report must end with a newline so the next log line starts clean",
            report.endsWith("\n"))
    }

    // --- The "Report a problem" share --------------------------------------

    @Test
    fun `the problem report subject carries the version, for triage from an inbox`() {
        val payload = buildProblemReport(ctx, logText = "")

        assertEquals("Tandem problem report — 1.4.0 (42)", payload.subject)
    }

    @Test
    fun `the problem report body repeats the facts, because the log may not attach`() {
        // The log is attached as a content:// URI. Plenty of targets (SMS, some
        // chat apps) silently drop the attachment and keep only the text, so the
        // version and device have to be in the body as well.
        val payload = buildProblemReport(ctx, logText = "10:00:00.000  I/Sync: hello\n")

        assertTrue(payload.body.contains("Tandem 1.4.0 (42)"))
        assertTrue(payload.body.contains("Google Pixel 7"))
        assertTrue(payload.body.contains("Android 14 (SDK 34)"))
        // A prompt, so the report is not just machine facts.
        assertTrue(payload.body.contains("What happened"))
    }

    @Test
    fun `a report with no log says so instead of pretending one is attached`() {
        val payload = buildProblemReport(ctx, logText = "")

        assertTrue(payload.body.contains("No diagnostic log"))
        assertFalse(payload.hasLog)
    }

    @Test
    fun `a report with a log flags it for attachment`() {
        assertTrue(buildProblemReport(ctx, logText = "something happened\n").hasLog)
    }
}
