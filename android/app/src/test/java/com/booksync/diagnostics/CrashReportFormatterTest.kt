package com.booksync.diagnostics

import org.junit.Assert.assertEquals
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
    //
    // Issue #636: there are two diagnostic channels (LogChannel.APP, always
    // available once anything has logged, and LogChannel.AUTO, which only has
    // content once someone turned on Android Auto diagnostics and used the
    // car). buildProblemReport takes the text read for each channel and says,
    // in the body, which ones are attached — and for anything missing, how to
    // turn it on, so a car report that arrives without the Auto log doesn't
    // read as "nothing to see" (that was exactly #612's shape).

    @Test
    fun `the problem report subject carries the version, for triage from an inbox`() {
        val payload = buildProblemReport(ctx, emptyMap())

        assertEquals("Tandem problem report — 1.4.0 (42)", payload.subject)
    }

    @Test
    fun `the problem report body repeats the facts, because a log may not attach`() {
        // Logs are attached as content:// URIs. Plenty of targets (SMS, some
        // chat apps) silently drop attachments and keep only the text, so the
        // version and device have to be in the body as well.
        val payload = buildProblemReport(ctx, mapOf(LogChannel.APP to "10:00:00.000  I/Sync: hello\n"))

        assertTrue(payload.body.contains("Tandem 1.4.0 (42)"))
        assertTrue(payload.body.contains("Google Pixel 7"))
        assertTrue(payload.body.contains("Android 14 (SDK 34)"))
        // A prompt, so the report is not just machine facts.
        assertTrue(payload.body.contains("What happened"))
    }

    @Test
    fun `the body names both logs when both are attached`() {
        val payload = buildProblemReport(
            ctx,
            mapOf(
                LogChannel.APP to "10:00:00.000  I/Sync: hello\n",
                LogChannel.AUTO to "10:01:00.000  I/Auto: onGetChildren 12ms\n",
            ),
        )

        assertTrue(payload.body.contains("Android Auto log and Tandem App log attached."))
        assertEquals(listOf(LogChannel.AUTO, LogChannel.APP), payload.attachedLogs)
    }

    @Test
    fun `with only the app log, the body names it and explains how to enable the missing Auto log`() {
        val payload = buildProblemReport(
            ctx,
            mapOf(LogChannel.APP to "10:00:00.000  I/Sync: hello\n", LogChannel.AUTO to ""),
        )

        assertTrue(payload.body.contains("Tandem App log attached."))
        assertTrue(payload.body.contains("No Android Auto log"))
        // Matches the Account screen's own path/wording (SectionTitle "Diagnostics",
        // ActionRow "Android Auto logs") so the instructions are findable as written.
        assertTrue(payload.body.contains("Account → Diagnostics → Android Auto logs"))
        assertEquals(listOf(LogChannel.APP), payload.attachedLogs)
    }

    @Test
    fun `a report with no logs says so instead of pretending one is attached, for both channels`() {
        val payload = buildProblemReport(ctx, mapOf(LogChannel.APP to "", LogChannel.AUTO to ""))

        assertTrue(payload.body.contains("No diagnostic logs attached."))
        assertTrue(payload.body.contains("Account → Diagnostics → Tandem app logs"))
        assertTrue(payload.body.contains("Account → Diagnostics → Android Auto logs"))
        assertTrue(payload.attachedLogs.isEmpty())
    }

    @Test
    fun `a missing key is treated the same as a blank log`() {
        // AccountViewModel reads every channel before calling this, but the
        // function itself should not assume the caller passed every key.
        val payload = buildProblemReport(ctx, mapOf(LogChannel.APP to "something happened\n"))

        assertEquals(listOf(LogChannel.APP), payload.attachedLogs)
        assertTrue(payload.body.contains("No Android Auto log"))
    }
}
