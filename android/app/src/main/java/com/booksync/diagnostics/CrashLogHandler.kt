package com.booksync.diagnostics

import java.time.Instant

/**
 * Writes an uncaught exception to the diagnostics log, then hands it on
 * (issue #230).
 *
 * The handler being replaced is the Android runtime's own — the one that logs
 * to logcat, reports to Play Vitals and kills the process. Not delegating to it
 * would leave a frozen app that never shows "Tandem keeps stopping" and never
 * reaches Vitals, which trades the aggregate data we do have for the manual
 * report we might get. So delegation is unconditional and the log write can
 * never prevent it: this runs while the process is already failing, and the
 * disk is one of the things that may be failing with it.
 *
 * [appendToLog] is a lambda rather than a [DiagnosticLogger] so the whole class
 * is JVM-testable; [BookSyncApp] passes `DiagnosticLogger::appendCrashReport`.
 */
class CrashLogHandler(
    private val previous: Thread.UncaughtExceptionHandler?,
    private val context: CrashContext,
    private val appendToLog: (String) -> Unit,
    private val nowIso: () -> String = { Instant.now().toString() },
) : Thread.UncaughtExceptionHandler {

    override fun uncaughtException(t: Thread, e: Throwable) {
        try {
            appendToLog(CrashReportFormatter.format(e, context, t.name, nowIso()))
        } catch (_: Throwable) {
            // Nothing useful to do — and nothing may be thrown from here, or the
            // real crash never reaches the platform handler.
        }
        previous?.uncaughtException(t, e)
    }

    companion object {
        /**
         * Install as the process-wide default, chaining onto whatever is already
         * registered. Idempotent: `BookSyncApp.onCreate` can run more than once
         * in a process (Android Auto starts the media service into it), and
         * chaining this handler onto itself would recurse forever inside the
         * crash path.
         */
        fun install(
            context: CrashContext,
            appendToLog: (String) -> Unit,
            nowIso: () -> String = { Instant.now().toString() },
        ): CrashLogHandler {
            val current = Thread.getDefaultUncaughtExceptionHandler()
            if (current is CrashLogHandler) return current
            val handler = CrashLogHandler(current, context, appendToLog, nowIso)
            Thread.setDefaultUncaughtExceptionHandler(handler)
            return handler
        }
    }
}
