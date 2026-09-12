package com.booksync.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What an unpaired ebook's or audiobook's overflow menu offers (issue #484).
 *
 * These two menus had the same three faults as the pair menu — a Listen row
 * gated on the file being present rather than on the book being playable, a
 * Reset progress row with no condition at all, and a Mark complete row the
 * caller could not switch off — but unlike the pair menu they made their
 * decisions inline inside the `@Composable`, where Compose's Kover exclusion
 * meant no test could reach them. Extracted here for the same reason
 * `pairMenuActions` and `primaryAction` were.
 *
 * The asymmetry between the two is deliberate and is the thing most likely to
 * be "tidied" into a bug later: an audiobook streams, an unpaired ebook does
 * not.
 */
class StandaloneMenuActionsTest {

    private fun ebook(
        downloaded: Boolean = true,
        paired: Boolean = false,
        complete: Boolean = false,
        hasProgress: Boolean = true,
    ) = OverflowTarget.Ebook(
        ebookId = 12,
        title = "Bartleby, the Scrivener",
        subtitle = "Herman Melville",
        isDownloaded = downloaded,
        isPaired = paired,
        isComplete = complete,
        hasProgress = hasProgress,
    )

    private fun audiobook(
        downloaded: Boolean = true,
        paired: Boolean = false,
        complete: Boolean = false,
        hasProgress: Boolean = true,
    ) = OverflowTarget.Audiobook(
        audiobookId = 34,
        title = "Bartleby, the Scrivener",
        subtitle = "Herman Melville",
        isDownloaded = downloaded,
        isPaired = paired,
        isComplete = complete,
        hasProgress = hasProgress,
    )

    // ---- the streaming gate ----

    @Test
    fun `a downloaded audiobook offers Listen`() {
        assertTrue(audiobookMenuActions(audiobook(), isOnline = true).contains(StandaloneAction.Open))
        assertTrue(audiobookMenuActions(audiobook(), isOnline = false).contains(StandaloneAction.Open))
    }

    /** The fix: the player streams an audiobook that is not on the device. */
    @Test
    fun `an audiobook that is not downloaded still offers Listen when online`() {
        assertTrue(
            audiobookMenuActions(audiobook(downloaded = false), isOnline = true)
                .contains(StandaloneAction.Open),
        )
    }

    @Test
    fun `offline, an audiobook that is not downloaded offers no Listen`() {
        assertFalse(
            audiobookMenuActions(audiobook(downloaded = false), isOnline = false)
                .contains(StandaloneAction.Open),
        )
    }

    /**
     * **Not** symmetrical with the audiobook, and not an oversight. A *paired*
     * ebook opens through `ReaderScreen`, which fetches the EPUB itself when it
     * is missing (issue #171). An unpaired one opens through
     * `StandaloneReaderScreen`, which deliberately has no download shell and
     * assumes its caller checked `isDownloaded` — offering Read here would open
     * a reader with no file.
     */
    @Test
    fun `an ebook that is not downloaded offers no Read, online or not`() {
        for (online in listOf(true, false)) {
            assertFalse(
                "StandaloneReaderScreen has no download shell (online=$online)",
                ebookMenuActions(ebook(downloaded = false)).contains(StandaloneAction.Open),
            )
        }
    }

    // ---- download / delete ----

    @Test
    fun `the download and the delete are the same slot`() {
        val absent = ebookMenuActions(ebook(downloaded = false))
        assertTrue(absent.contains(StandaloneAction.Download))
        assertFalse(absent.contains(StandaloneAction.Delete))

        val present = ebookMenuActions(ebook(downloaded = true))
        assertTrue(present.contains(StandaloneAction.Delete))
        assertFalse(present.contains(StandaloneAction.Download))
    }

    // ---- the rows that had no condition ----

    @Test
    fun `a book with no progress is not offered a reset`() {
        assertFalse(ebookMenuActions(ebook(hasProgress = false)).contains(StandaloneAction.ResetProgress))
        assertTrue(ebookMenuActions(ebook(hasProgress = true)).contains(StandaloneAction.ResetProgress))
        assertFalse(
            audiobookMenuActions(audiobook(hasProgress = false), true)
                .contains(StandaloneAction.ResetProgress),
        )
    }

    @Test
    fun `a completed book is not asked to be completed again`() {
        assertFalse(ebookMenuActions(ebook(complete = true)).contains(StandaloneAction.MarkComplete))
        assertTrue(ebookMenuActions(ebook(complete = false)).contains(StandaloneAction.MarkComplete))
        assertFalse(
            audiobookMenuActions(audiobook(complete = true), true)
                .contains(StandaloneAction.MarkComplete),
        )
    }

    // ---- unchanged behaviour, so the extraction cannot quietly drop a row ----

    @Test
    fun `pairing is offered only while the book is unpaired`() {
        assertTrue(ebookMenuActions(ebook(paired = false)).contains(StandaloneAction.PairWith))
        assertFalse(ebookMenuActions(ebook(paired = true)).contains(StandaloneAction.PairWith))
        assertTrue(audiobookMenuActions(audiobook(paired = false), true).contains(StandaloneAction.PairWith))
    }

    @Test
    fun `view details always leads`() {
        assertEquals(StandaloneAction.ViewDetails, ebookMenuActions(ebook()).first())
        assertEquals(StandaloneAction.ViewDetails, audiobookMenuActions(audiobook(), true).first())
    }
}
