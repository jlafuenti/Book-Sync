package com.booksync.ui.account

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #963: the Account screen offers the demo account none of the controls
 * the server refuses it — Change password, Sign out everywhere, Delete account —
 * and says why instead.
 *
 * The decision is `canChangeOwnAccount` (pinned in `RoleGateTest`); the screen is
 * drawn inline in `@Composable`s excluded from Kover, so the wiring is pinned by
 * source guards, as in `ReportProblemDiscordWiringTest`.
 */
class DemoAccountWiringTest {

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
    fun `the screen asks the role gate whether the account can change itself`() {
        assertTrue(screen().contains("canChangeOwnAccount(user?.role)"))
    }

    @Test
    fun `sign out everywhere and delete account are behind the gate`() {
        val source = screen()
        val gated = source.substringAfter("if (canChangeAccount) {", "")
        assertTrue("No gated block found.", gated.isNotEmpty())
        val signOutAll = source.indexOf("title = \"Sign out everywhere\"")
        val delete = source.indexOf("title = stringResource(R.string.delete_account_title)")
        val gate = source.indexOf("if (canChangeAccount) {")
        assertTrue("Sign out everywhere must sit inside the gate.", gate in 0 until signOutAll)
        assertTrue("Delete account must sit inside the gate.", gate in 0 until delete)
    }

    @Test
    fun `the change password button is behind the gate and the demo note replaces it`() {
        val source = screen()
        assertTrue(source.contains("canChangePassword = canChangeAccount"))
        val card = source.substringAfter("private fun UserCard(")
        assertTrue(card.contains("if (canChangePassword) {"))
        assertTrue(card.contains("R.string.account_demo_note"))
    }

    @Test
    fun `the demo note says why`() {
        var dir = File("").absoluteFile
        var strings: File? = null
        repeat(4) {
            listOf("app/src/main/res/values/strings.xml", "src/main/res/values/strings.xml")
                .map { File(dir, it) }.firstOrNull { it.exists() }?.let { strings = it }
            dir = dir.parentFile ?: return@repeat
        }
        val text = strings?.readText() ?: throw AssertionError("strings.xml not found")
        val note = Regex("<string name=\"account_demo_note\">([^<]*)</string>").find(text)
        assertEquals(true, note?.groupValues?.get(1)?.contains("shared demo account"))
    }
}
