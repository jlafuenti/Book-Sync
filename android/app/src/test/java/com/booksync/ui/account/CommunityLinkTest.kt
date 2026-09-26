package com.booksync.ui.account

import com.booksync.diagnostics.COMMUNITY_DISCORD_URL
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #714: the community Discord, reachable from the Account screen.
 *
 * The URL is a constant beside the support address, and the web holds the same
 * value (`web/src/lib/community.js`). Opening it is Android glue (an
 * `ACTION_VIEW` Intent) with nothing to run on the JVM, so the row's wiring is
 * pinned by source guards, as in `BookDetailsGatingWiringTest`.
 */
class CommunityLinkTest {

    @Test
    fun `the invite is the project's permanent Discord link`() {
        assertEquals("https://discord.gg/nt9xFKFus", COMMUNITY_DISCORD_URL)
    }

    private fun source(rel: String): String {
        var dir = File("").absoluteFile
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
    fun `the account screen offers the Discord row`() {
        val screen = source("com/booksync/ui/account/AccountScreen.kt")

        assertTrue(
            "The Account screen must show the community row (issue #714).",
            screen.contains("R.string.account_community_discord") &&
                screen.contains("openCommunityDiscord("),
        )
    }

    @Test
    fun `opening the link cannot crash a device with no browser`() {
        val screen = source("com/booksync/ui/account/AccountScreen.kt")
        val helper = screen.substringAfter("fun openCommunityDiscord(", "")

        assertTrue("openCommunityDiscord must exist", helper.isNotEmpty())
        assertTrue(
            "It must open the constant with ACTION_VIEW.",
            helper.contains("Intent.ACTION_VIEW") && helper.contains("COMMUNITY_DISCORD_URL"),
        )
        assertTrue(
            "startActivity throws when nothing resolves a web link (the test emulator " +
                "has no browser at all); it must be caught, not crash the app.",
            helper.contains("ActivityNotFoundException"),
        )
    }
}
