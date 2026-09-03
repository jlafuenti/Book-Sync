package com.booksync.diagnostics

import java.io.PrintWriter
import java.io.StringWriter

/**
 * Crash reporting for a self-hosted app with no crash SDK (issue #230).
 *
 * Everything in this file is deliberately free of Android types so it can be
 * unit-tested on the JVM: the two facts that come from the platform
 * ([android.os.Build] and `BuildConfig`) are passed in as a [CrashContext],
 * assembled at the call site.
 *
 * Why no Crashlytics/Sentry: the app is a client for a server its user runs,
 * and a third-party crash processor drags in a privacy-policy and Play Data
 * safety obligation that is not worth rushing before the first release. What is
 * left is Play Vitals — aggregate only, days late, and nothing at all from
 * users who opted out of usage sharing. So the fallback is a report the user can
 * hand over: the trace is written to the existing diagnostics log as the process
 * dies, and "Report a problem" on the Account screen shares it.
 */

/** The facts a stack trace is useless without. */
data class CrashContext(
    val versionName: String,
    val versionCode: Int,
    val deviceManufacturer: String,
    val deviceModel: String,
    val androidRelease: String,
    val androidSdkInt: Int,
) {
    /** "Google Pixel 7" — the manufacturer is dropped when the model repeats it. */
    val device: String
        get() = if (deviceModel.startsWith(deviceManufacturer, ignoreCase = true)) deviceModel
                else "$deviceManufacturer $deviceModel"

    /** "Tandem 1.4.0 (42)" — the versionCode is what a `mapping.txt` is filed under. */
    val appVersion: String get() = "Tandem $versionName ($versionCode)"

    /** "14 (SDK 34)" — the release is what a user reads, the SDK is what an API guard checks. */
    val androidVersion: String get() = "$androidRelease (SDK $androidSdkInt)"
}

object CrashReportFormatter {

    const val START_MARKER = "=== Tandem crash ==="
    const val END_MARKER = "=== end of crash ==="

    /**
     * The block appended to the diagnostics log when the process is about to die.
     *
     * Markers on both ends because this lands in the middle of a rolling log
     * that the user may have had running for an hour: without them the trace is
     * indistinguishable from ordinary output once it has been pasted into an
     * email.
     */
    fun format(
        throwable: Throwable,
        context: CrashContext,
        threadName: String,
        timestampIso: String,
    ): String = buildString {
        appendLine(START_MARKER)
        appendLine("Time: $timestampIso")
        appendLine("App: ${context.appVersion}")
        appendLine("Device: ${context.device}")
        appendLine("Android: ${context.androidVersion}")
        appendLine("Thread: $threadName")
        append(stackTraceOf(throwable))
        appendLine(END_MARKER)
    }

    /** `Throwable.printStackTrace` into a string — cause chain and all. */
    private fun stackTraceOf(throwable: Throwable): String {
        val writer = StringWriter()
        PrintWriter(writer).use { throwable.printStackTrace(it) }
        return writer.toString()
    }
}

/** Subject, body and whether a log file is worth attaching. */
data class ProblemReport(
    val subject: String,
    val body: String,
    val hasLog: Boolean,
)

/**
 * The text half of the "Report a problem" share.
 *
 * The version and device are repeated in the body on purpose: the log goes as a
 * `content://` attachment, and share targets are free to drop attachments —
 * several do, silently. A report that arrives as body-only should still be
 * triageable.
 */
fun buildProblemReport(context: CrashContext, logText: String): ProblemReport {
    val hasLog = logText.isNotBlank()
    val body = buildString {
        appendLine("What happened? (please describe, and roughly when)")
        appendLine()
        appendLine()
        appendLine("---")
        appendLine(context.appVersion)
        appendLine(context.device)
        appendLine("Android ${context.androidVersion}")
        appendLine(
            if (hasLog) "Diagnostic log attached."
            else "No diagnostic log — turn on Account → Diagnostics → Tandem app logs, " +
                "reproduce the problem, then report again.",
        )
    }
    return ProblemReport(
        subject = "Tandem problem report — ${context.versionName} (${context.versionCode})",
        body = body,
        hasLog = hasLog,
    )
}
