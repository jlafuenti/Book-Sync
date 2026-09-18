package com.booksync.diagnostics

/**
 * What "Report a problem" should build — kept free of Android types (no
 * `Intent`, no `Context`) so it is unit-testable on the JVM, same pattern as
 * `ServerUrlPolicy`/`PositionSavePolicy`. The Android glue that turns a
 * [ReportProblemPlan] into a real `Intent` lives in
 * `ui/account/AccountViewModel.kt` and is not unit-tested, for the same reason
 * [buildProblemReport] above stops where it does: nothing below that point
 * (PackageManager, FileProvider, Intent extras) survives the JVM unit-test
 * android.jar stub.
 */

/** The project's public support address (issue #609). Also documented in `docs/privacy.md`. */
const val SUPPORT_EMAIL = "support@tandembook.com"

/** Fixed on purpose (issue #609) — the version/device facts belong in the body, not the subject. */
const val REPORT_PROBLEM_EMAIL_SUBJECT = "Report a Problem"

/**
 * Everything needed to build the "Report a problem" intent, decided before any
 * Android type gets involved.
 *
 * @param toMailApp true when a mail app resolved and the report should go
 *   straight to one addressed at [SUPPORT_EMAIL]; false when no mail app is
 *   installed and the caller must fall back to today's generic share sheet
 *   (issue #609 — a dead button is worse than an unrouted share).
 */
data class ReportProblemPlan(
    val toMailApp: Boolean,
    val recipient: String,
    val subject: String,
    val body: String,
    val attachLog: Boolean,
)

/**
 * Decide what "Report a problem" should build from the already-formatted
 * [report] and whether a mail app resolved on the device.
 *
 * The subject differs by branch on purpose: addressed straight to a mail app,
 * it is the fixed, triage-friendly [REPORT_PROBLEM_EMAIL_SUBJECT]; falling
 * back to the generic share sheet keeps [ProblemReport.subject] (the
 * version-bearing subject `buildProblemReport` already produces), because that
 * sheet has no "To" field to carry the destination and a share target — Notes,
 * a messaging app — is exactly where the version needs to travel in the text.
 */
fun planReportProblemIntent(report: ProblemReport, mailAppAvailable: Boolean): ReportProblemPlan =
    ReportProblemPlan(
        toMailApp = mailAppAvailable,
        recipient = SUPPORT_EMAIL,
        subject = if (mailAppAvailable) REPORT_PROBLEM_EMAIL_SUBJECT else report.subject,
        body = report.body,
        attachLog = report.hasLog,
    )
