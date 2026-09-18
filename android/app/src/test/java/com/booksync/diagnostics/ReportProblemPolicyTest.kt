package com.booksync.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What "Report a problem" builds (issue #609): an email pre-filled to support
 * when a mail app is available, today's generic share sheet as a fallback so
 * the button is never dead on a device with none installed.
 *
 * This only pins the decision — [ReportProblemPlan] — not the `Intent` it
 * becomes; see [ReportProblemPlan]'s doc for why that boundary is where it is.
 */
class ReportProblemPolicyTest {

    private val ctx = CrashContext(
        versionName = "1.4.0",
        versionCode = 42,
        deviceManufacturer = "Google",
        deviceModel = "Pixel 7",
        androidRelease = "14",
        androidSdkInt = 34,
    )

    @Test
    fun `with a mail app, the plan targets support with a fixed subject`() {
        val report = buildProblemReport(ctx, logText = "10:00:00.000  I/Sync: hello\n")

        val plan = planReportProblemIntent(report, mailAppAvailable = true)

        assertTrue(plan.toMailApp)
        assertEquals(SUPPORT_EMAIL, plan.recipient)
        assertEquals("Report a Problem", plan.subject)
    }

    @Test
    fun `with a mail app, the body is buildProblemReport's — version, device, log summary`() {
        val report = buildProblemReport(ctx, logText = "10:00:00.000  I/Sync: hello\n")

        val plan = planReportProblemIntent(report, mailAppAvailable = true)

        assertEquals(report.body, plan.body)
        assertTrue(plan.body.contains("Tandem 1.4.0 (42)"))
        assertTrue(plan.body.contains("Google Pixel 7"))
        // The "describe what went wrong" prompt buildProblemReport already puts
        // at the top of the body.
        assertTrue(plan.body.startsWith("What happened?"))
    }

    @Test
    fun `with a mail app, a log attaches`() {
        val report = buildProblemReport(ctx, logText = "something happened\n")

        assertTrue(planReportProblemIntent(report, mailAppAvailable = true).attachLog)
    }

    @Test
    fun `with a mail app, no log means nothing attaches`() {
        val report = buildProblemReport(ctx, logText = "")

        assertFalse(planReportProblemIntent(report, mailAppAvailable = true).attachLog)
    }

    @Test
    fun `with no mail app, the plan falls back to the share sheet with the dynamic subject`() {
        val report = buildProblemReport(ctx, logText = "10:00:00.000  I/Sync: hello\n")

        val plan = planReportProblemIntent(report, mailAppAvailable = false)

        assertFalse(plan.toMailApp)
        // No "To" field on a generic share sheet, so the version-bearing subject
        // buildProblemReport already produces is what carries that fact.
        assertEquals(report.subject, plan.subject)
        assertEquals("Tandem problem report — 1.4.0 (42)", plan.subject)
    }

    @Test
    fun `with no mail app, the log still attaches when one exists`() {
        val report = buildProblemReport(ctx, logText = "something happened\n")

        assertTrue(planReportProblemIntent(report, mailAppAvailable = false).attachLog)
    }
}
