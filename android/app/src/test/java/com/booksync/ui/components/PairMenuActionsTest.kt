package com.booksync.ui.components

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What a pair's overflow menu offers (issues #333 and #170).
 *
 * Two problems in one menu, which is why they are fixed together.
 *
 * **#333** — the menu listed "Delete ebook" *and* "Delete audiobook". For a pair
 * the user is holding one book, so freeing its space meant two taps through two
 * confirmations. Worse, those two destructive rows sat directly above **"Unlink
 * pair"**, which is not a local delete at all: it breaks the ebook↔audiobook
 * pairing on the server, for every device. Similar names, very different blast
 * radii, no separation.
 *
 * **#170** — "Unlink pair" was shown to every role. A plain `user` tapped it,
 * the editor-gated endpoint answered 403, and the snackbar showed Retrofit's raw
 * `"HTTP 403 "`. The web never renders it for that user.
 *
 * Extracted as a pure function because `CardOverflowMenu` is a Composable and
 * Compose is excluded from Kover — the decision could not otherwise carry a
 * test. Same move `primaryAction` needed for #169.
 */
class PairMenuActionsTest {

    private fun pair(
        ebook: Boolean = true,
        audio: Boolean = true,
        transcribed: Boolean = true,
        queued: Boolean = false,
        complete: Boolean = false,
    ) = OverflowTarget.Pair(
        pairId = 84,
        title = "The Mad Ship",
        subtitle = "Robin Hobb",
        hasEbookDownloaded = ebook,
        hasAudiobookDownloaded = audio,
        isTranscribed = transcribed,
        isQueuedOrTranscribing = queued,
        isComplete = complete,
    )

    // ---- #333: one delete, not two ----

    @Test
    fun `a downloaded pair offers a single delete`() {
        val actions = pairMenuActions(pair(), canUnlink = true, isOnline = true)
        assertEquals(
            "a pair is one book; deleting it should not take two taps",
            1, actions.count { it == PairAction.DeletePair },
        )
        assertTrue(actions.contains(PairAction.DeletePair))
    }

    @Test
    fun `the delete appears whenever either file is present`() {
        // Half-downloaded pairs still take up space and must be clearable.
        assertTrue(pairMenuActions(pair(ebook = true, audio = false), true, true).contains(PairAction.DeletePair))
        assertTrue(pairMenuActions(pair(ebook = false, audio = true), true, true).contains(PairAction.DeletePair))
    }

    @Test
    fun `nothing downloaded means nothing to delete`() {
        assertFalse(
            pairMenuActions(pair(ebook = false, audio = false), true, true)
                .contains(PairAction.DeletePair),
        )
    }

    @Test
    fun `unlink is not adjacent to the delete`() {
        // The mis-tap this issue is about: one removes local files and is undone
        // by re-downloading, the other unpairs server-side for every device.
        val actions = pairMenuActions(pair(), canUnlink = true, isOnline = true)
        assertTrue("both should be present in this fixture", actions.contains(PairAction.DeletePair))
        // Last, not merely "a couple of rows away" — a mutation that moved it up
        // to two positions after the delete slipped past the weaker check.
        assertEquals(
            "Unlink pair belongs at the end, away from the local delete",
            PairAction.UnlinkPair, actions.last(),
        )
    }

    // ---- #170: editor-only actions ----

    @Test
    fun `a user who cannot edit is not offered the unlink`() {
        val actions = pairMenuActions(pair(), canUnlink = false, isOnline = true)
        assertFalse(
            "a plain user tapping this got a raw HTTP 403 snackbar",
            actions.contains(PairAction.UnlinkPair),
        )
    }

    @Test
    fun `an editor still gets the unlink`() {
        assertTrue(pairMenuActions(pair(), canUnlink = true, isOnline = true).contains(PairAction.UnlinkPair))
    }

    @Test
    fun `losing the unlink does not remove anything else`() {
        // Gating must hide exactly one row, not quietly strip the menu.
        val editor = pairMenuActions(pair(), canUnlink = true, isOnline = true)
        val plain = pairMenuActions(pair(), canUnlink = false, isOnline = true)
        assertEquals(listOf(PairAction.UnlinkPair), editor - plain.toSet())
    }

    // ---- the rest of the menu, so the refactor cannot quietly drop a row ----

    @Test
    fun `read and listen follow what is downloaded`() {
        val both = pairMenuActions(pair(), true, true)
        assertTrue(both.contains(PairAction.Read))
        assertTrue(both.contains(PairAction.Listen))

        val ebookOnly = pairMenuActions(pair(audio = false), true, true)
        assertTrue(ebookOnly.contains(PairAction.Read))
        assertFalse(ebookOnly.contains(PairAction.Listen))
    }

    @Test
    fun `a partially downloaded pair still offers the download`() {
        assertTrue(pairMenuActions(pair(audio = false), true, true).contains(PairAction.DownloadPair))
        assertFalse(pairMenuActions(pair(), true, true).contains(PairAction.DownloadPair))
    }

    @Test
    fun `transcription state picks exactly one row`() {
        assertTrue(pairMenuActions(pair(transcribed = true), true, true).contains(PairAction.RefreshSyncData))
        assertTrue(
            pairMenuActions(pair(transcribed = false, queued = true), true, true)
                .contains(PairAction.CancelTranscription),
        )
        assertTrue(
            pairMenuActions(pair(transcribed = false), true, true).contains(PairAction.Transcribe),
        )
    }

    @Test
    fun `a completed pair is not asked to be completed again`() {
        assertFalse(pairMenuActions(pair(complete = true), true, true).contains(PairAction.MarkComplete))
        assertTrue(pairMenuActions(pair(complete = false), true, true).contains(PairAction.MarkComplete))
    }

    // ------------------------------------------------------------------
    // Wiring. Every assertion above passes with a decision nothing consults:
    // a correct function, and a menu still showing two deletes and an unlink
    // to everyone. Four guards have shipped inert in this repository already.
    // ------------------------------------------------------------------

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath")
    }

    @Test
    fun `the menu renders from the decision, not its own conditionals`() {
        val menu = source("com/booksync/ui/components/CardOverflowMenu.kt")
        assertTrue(
            "PairActions must render from pairMenuActions — otherwise this whole " +
                "file tests a function nobody calls.",
            menu.contains("pairMenuActions("),
        )
        assertFalse(
            "the pair menu must no longer offer two separate deletes (issue #333)",
            menu.contains("\"Delete ebook\", destructive = true, onClick = onConfirmDeleteEb"),
        )
    }

    @Test
    fun `every pair call site gates the unlink on canEdit`() {
        // The gate lives at the call sites: OverflowActions.onUnlinkPair == null
        // is what hides the row. A site that wires it unconditionally reopens
        // the bug for that screen only, which is easy to miss.
        for (path in listOf(
            "com/booksync/ui/library/LibraryScreen.kt",
            "com/booksync/ui/downloaded/DownloadedScreen.kt",
        )) {
            val text = source(path)
            val line = text.lines().first { it.contains("onUnlinkPair") }
            assertTrue(
                "$path wires onUnlinkPair without consulting canEdit: $line",
                line.contains("canEdit"),
            )
        }
    }

    @Test
    fun `the pair-only surfaces consult the role at all`() {
        for (path in listOf(
            "com/booksync/ui/library/LibraryScreen.kt",
            "com/booksync/ui/downloaded/DownloadedScreen.kt",
            "com/booksync/ui/library/SearchScreen.kt",
            "com/booksync/ui/details/BookDetailsScreen.kt",
        )) {
            assertTrue("$path never reads canEdit", source(path).contains("canEdit"))
        }
    }

    @Test
    fun `the role actually reaches the app`() {
        // hasMinRole over a role nothing ever stores is a permanent "no".
        assertTrue(
            "the role must be persisted when /auth/me answers",
            source("com/booksync/data/remote/TokenManager.kt").contains("saveRole"),
        )
        assertTrue(
            "login must fetch the role",
            source("com/booksync/ui/auth/LoginScreen.kt").contains("saveRole"),
        )
        assertTrue(
            "an already-signed-in user must get their role refreshed at startup",
            source("com/booksync/BookSyncApp.kt").contains("saveRole"),
        )
        assertTrue(
            "the role must be cleared with the session",
            source("com/booksync/data/remote/TokenManager.kt").contains("prefs.remove(KEY_ROLE)"),
        )
    }
}
