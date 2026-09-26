package com.booksync.ui.account

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #715: Report a problem offers Discord next to email.
 *
 * A tester on a phone with no mail app got the generic share sheet, chose
 * Discord, and it did not load on the first try. Discord is now a named choice
 * that does not depend on sharing into the Discord app: the report is copied as
 * text and the community Discord opened, to be pasted into a new forum post.
 * Email is unchanged and still the route that carries the logs.
 *
 * The text is pinned in `ReportProblemPolicyTest`; the dialog and the Android
 * glue (clipboard, Intent) are drawn inline in `@Composable`s excluded from
 * Kover, so they are pinned by source guards, as in `CommunityLinkTest`.
 */
class ReportProblemDiscordWiringTest {

    private fun screen(): String {
        var dir = File("").absoluteFile
        val rel = "com/booksync/ui/account/AccountScreen.kt"
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$rel")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$rel")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $rel from ${File("").absolutePath}")
    }

    @Test
    fun `Report a problem asks where to send the report`() {
        val source = screen()
        val row = source.substringAfter("title = stringResource(R.string.account_report_problem),")
            .substringBefore("ActionRow(")

        assertTrue(
            "The row must open the choice rather than go straight to email.",
            row.contains("showReportChoice = true"),
        )
        assertTrue(
            "The choice must offer both destinations.",
            source.contains("R.string.report_problem_email") && source.contains("R.string.report_problem_discord"),
        )
    }

    @Test
    fun `email is unchanged and Discord copies the text, then opens the community`() {
        val source = screen()

        assertTrue(
            "Email must still go through shareProblemReport, the route that attaches logs.",
            source.contains("viewModel.shareProblemReport"),
        )
        assertTrue(
            "Discord must use the text built for it.",
            source.contains("reportToDiscord(") && source.contains("viewModel.problemReportTextForDiscord()"),
        )
        val helper = source.substringAfter("fun reportToDiscord(", "")
        assertTrue("reportToDiscord must exist", helper.isNotEmpty())
        assertTrue(
            "It must put the report on the clipboard.",
            helper.contains("setPrimaryClip"),
        )
        assertTrue(
            "It must open the community Discord through the crash-safe helper.",
            helper.contains("openCommunityDiscord("),
        )
    }
}
