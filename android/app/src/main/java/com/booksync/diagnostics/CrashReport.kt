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

/** Subject, body and which diagnostic channels are worth attaching. */
data class ProblemReport(
    val subject: String,
    val body: String,
    val attachedLogs: List<LogChannel>,
)

/**
 * The text half of the "Report a problem" share.
 *
 * Two diagnostic channels exist ([LogChannel]): [LogChannel.APP], which has
 * content once anything has logged, and [LogChannel.AUTO], which only has
 * content once someone turned on Android Auto diagnostics from the Account
 * screen and used the car. A missing Auto log is normal, not an error (issue
 * #636) — a car report that silently arrives without it reads as "nothing to
 * see", which is exactly the shape #612 turned out to be. So the body says
 * which logs are attached, and for anything missing, how to turn it on.
 *
 * [logTextByChannel] is keyed by whichever channels the caller read; a channel
 * left out is treated the same as blank text for it.
 *
 * The version and device are repeated in the body on purpose: logs go as
 * `content://` attachments, and share targets are free to drop attachments —
 * several do, silently. A report that arrives as body-only should still be
 * triageable.
 */
fun buildProblemReport(context: CrashContext, logTextByChannel: Map<LogChannel, String>): ProblemReport {
    val attachedLogs = LogChannel.entries.filter { logTextByChannel[it]?.isNotBlank() == true }
    val missingLogs = LogChannel.entries.filter { it !in attachedLogs }

    val body = buildString {
        appendLine("What happened? (please describe, and roughly when)")
        appendLine()
        appendLine()
        appendLine("---")
        appendLine(context.appVersion)
        appendLine(context.device)
        appendLine("Android ${context.androidVersion}")
        appendLine(attachedLogsSummary(attachedLogs))
        missingLogs.forEach { appendLine(missingLogHint(it)) }
    }
    return ProblemReport(
        subject = "Tandem problem report — ${context.versionName} (${context.versionCode})",
        body = body,
        attachedLogs = attachedLogs,
    )
}

private fun attachedLogsSummary(attachedLogs: List<LogChannel>): String = when {
    attachedLogs.isEmpty() -> "No diagnostic logs attached."
    else -> "${attachedLogs.joinToString(" and ") { "${it.label} log" }} attached."
}

/**
 * How to turn on [channel], worded to match the Account screen exactly
 * (`SectionTitle("Diagnostics")`, `ActionRow` titles "Android Auto logs" /
 * "Tandem app logs" in `AccountScreen.kt`) so the instructions are findable as
 * written.
 */
private fun missingLogHint(channel: LogChannel): String = when (channel) {
    LogChannel.AUTO -> "No Android Auto log — turn on Account → Diagnostics → Android Auto logs, " +
        "reproduce the problem in the car, then report again."
    LogChannel.APP -> "No Tandem app log — turn on Account → Diagnostics → Tandem app logs, " +
        "reproduce the problem, then report again."
}
