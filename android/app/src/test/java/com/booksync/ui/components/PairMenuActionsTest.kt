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
        syncMapCached: Boolean = true,
        hasProgress: Boolean = true,
    ) = OverflowTarget.Pair(
        pairId = 84,
        title = "The Mad Ship",
        subtitle = "Robin Hobb",
        hasEbookDownloaded = ebook,
        hasAudiobookDownloaded = audio,
        isTranscribed = transcribed,
        isQueuedOrTranscribing = queued,
        isComplete = complete,
        syncMapCached = syncMapCached,
        hasProgress = hasProgress,
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

    // ---- #484: openable, not downloaded ----

    /**
     * Before issue #171 "is it on the device" and "can I open it" were the same
     * question, so gating these rows on the download flag was right. Streaming
     * made them different questions and nothing revisited this menu: the only
     * row that reaches the player stayed behind `hasAudiobookDownloaded`, so a
     * book the player would happily stream offered no way to start it. The only
     * route left was to open the ebook and use the reader's switch-to-audiobook
     * action, which is not a route anyone would find.
     */
    @Test
    fun `read and listen follow what can be opened, not what is downloaded`() {
        val both = pairMenuActions(pair(), true, true)
        assertTrue(both.contains(PairAction.Read))
        assertTrue(both.contains(PairAction.Listen))

        val nothingLocal = pairMenuActions(pair(ebook = false, audio = false), true, isOnline = true)
        assertTrue(
            "the audiobook streams — this is the missing option the menu never offered",
            nothingLocal.contains(PairAction.Listen),
        )
        assertTrue(
            "the reader fetches the EPUB on open (issue #171), so this works too",
            nothingLocal.contains(PairAction.Read),
        )
    }

    /**
     * The other half of the same rule: offline, "openable" collapses back to
     * "on the device". Offering a stream with no network is the same defect in
     * the opposite direction — an action that cannot succeed.
     */
    @Test
    fun `offline, only what is on the device can be opened`() {
        val offline = pairMenuActions(pair(ebook = false, audio = false), true, isOnline = false)
        assertFalse(offline.contains(PairAction.Read))
        assertFalse(offline.contains(PairAction.Listen))

        val audioOnly = pairMenuActions(pair(ebook = false, audio = true), true, isOnline = false)
        assertTrue(audioOnly.contains(PairAction.Listen))
        assertFalse(audioOnly.contains(PairAction.Read))
    }

    /**
     * "Re-download the latest sync map" — with no map cached there is nothing to
     * re-download. `isTranscribed` means the *server* holds a transcript, which
     * is a different fact; `DownloadedScreen` already gated on the local cache,
     * so the same pair offered the row on one screen and not the other.
     */
    @Test
    fun `there is nothing to refresh without a cached sync map`() {
        assertFalse(
            pairMenuActions(pair(syncMapCached = false), true, true)
                .contains(PairAction.RefreshSyncData),
        )
        assertTrue(
            pairMenuActions(pair(syncMapCached = true), true, true)
                .contains(PairAction.RefreshSyncData),
        )
    }

    /**
     * The gate above must not fall through. The transcription rows are a
     * three-way `when`, so dropping the refresh naively lands on `else ->
     * Transcribe` and offers to transcribe a pair that already is transcribed.
     */
    @Test
    fun `a transcribed pair is never offered transcription again`() {
        val actions = pairMenuActions(pair(transcribed = true, syncMapCached = false), true, true)
        assertFalse(actions.contains(PairAction.Transcribe))
        assertFalse(actions.contains(PairAction.CancelTranscription))
        assertFalse(actions.contains(PairAction.RefreshSyncData))
    }

    /**
     * Reset progress was unconditional — `add(PairAction.ResetProgress)`, no
     * condition at all — and the target carried no progress field, so the menu
     * could not have decided even if it had tried. It was offered on books that
     * had never been opened.
     */
    @Test
    fun `a book with no progress is not offered a reset`() {
        assertFalse(
            pairMenuActions(pair(hasProgress = false), true, true)
                .contains(PairAction.ResetProgress),
        )
        assertTrue(
            pairMenuActions(pair(hasProgress = true), true, true)
                .contains(PairAction.ResetProgress),
        )
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
