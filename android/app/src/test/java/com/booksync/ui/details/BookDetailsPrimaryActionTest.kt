package com.booksync.ui.details

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * What the detail page's one big button offers (issue #169).
 *
 * The bug this pins: a *standalone* ebook — one with no paired audiobook, which
 * is most of the library — offered "Download ebook", downloaded successfully,
 * and then the button **disappeared**. The page was left with Delete / Reset /
 * Mark complete and no way to read the book that had just been fetched. Tapping
 * the same book from Home or Downloaded did nothing either. Both web surfaces
 * open and read that identical file.
 *
 * The decision was a `when` inside a Composable, so nothing could assert it
 * without an instrumentation test. It is a pure function now, and this file is
 * the reason: the branch returning `null` looked deliberate — it even carried a
 * comment saying so — and would have survived any amount of code review.
 */
class BookDetailsPrimaryActionTest {

    private fun ebook(id: Int = 7, downloaded: Boolean) = EBookEntity(
        id = id,
        title = "Skeleton Crew",
        author = "Stephen King",
        filename = "skeleton-crew.epub",
        fileSize = 1_024L,
        format = "epub",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00Z",
        isDownloaded = downloaded,
    )

    private fun audiobook(id: Int = 9, downloaded: Boolean) = AudioBookEntity(
        id = id,
        title = "Bartleby, the Scrivener",
        author = "Herman Melville",
        filename = "bartleby.mp3",
        durationSeconds = 3_429,
        format = "mp3",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00Z",
        isDownloaded = downloaded,
    )

    private fun pair(ebookDownloaded: Boolean, audiobookDownloaded: Boolean) = BookPairEntity(
        id = 84,
        ebookId = 7,
        ebookTitle = "The Mad Ship",
        ebookAuthor = "Robin Hobb",
        ebookFilename = "mad-ship.epub",
        ebookFormat = "epub",
        audiobookId = 9,
        audiobookTitle = "The Mad Ship",
        audiobookAuthor = "Robin Hobb",
        audiobookFilename = "mad-ship.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = 1_000,
        status = "ready",
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
    )

    // ---- the regression -------------------------------------------------

    @Test
    fun `a downloaded standalone ebook offers to read it`() {
        val ui = BookDetailsUi(loading = false, ebook = ebook(downloaded = true))
        assertEquals(PrimaryAction.ReadStandalone, primaryAction(ui))
    }

    @Test
    fun `a standalone ebook is never left with no action after downloading`() {
        // The shape of the bug, independent of which action is right: offering a
        // download that resolves to an empty slot is the defect. Stated
        // separately from the assertion above so that swapping ReadStandalone
        // for some other entry point cannot quietly reintroduce the dead end.
        val downloaded = BookDetailsUi(loading = false, ebook = ebook(downloaded = true))
        assertNotNull(
            "downloading a standalone ebook must not lead to an empty button slot",
            primaryAction(downloaded),
        )
    }

    // ---- everything that already worked, so the fix cannot break it ------

    @Test
    fun `a standalone ebook that is not downloaded offers the download`() {
        val ui = BookDetailsUi(loading = false, ebook = ebook(downloaded = false))
        assertEquals(PrimaryAction.DownloadEbook, primaryAction(ui))
    }

    @Test
    fun `a standalone audiobook keeps its listen and download actions`() {
        assertEquals(
            PrimaryAction.ListenStandalone,
            primaryAction(BookDetailsUi(loading = false, audiobook = audiobook(downloaded = true))),
        )
        assertEquals(
            PrimaryAction.DownloadAudiobook,
            primaryAction(BookDetailsUi(loading = false, audiobook = audiobook(downloaded = false))),
        )
    }

    @Test
    fun `a pair prefers the reader, then the player`() {
        assertEquals(
            PrimaryAction.Read,
            primaryAction(BookDetailsUi(loading = false, pair = pair(true, true))),
        )
        assertEquals(
            PrimaryAction.Listen,
            primaryAction(BookDetailsUi(loading = false, pair = pair(false, true))),
        )
    }

    // ---- #484: this screen had no way to reach the player -----------------

    /**
     * A pair with nothing downloaded used to offer only "Download pair", and
     * nothing else on the page navigated to the player either — so the details
     * screen, the obvious place to start a book, was the one surface from which
     * an undownloaded book could not be played at all. The player streams it
     * (issue #171); only this button stood in the way.
     */
    @Test
    fun `a pair with nothing downloaded offers to stream it`() {
        assertEquals(
            PrimaryAction.Listen,
            primaryAction(BookDetailsUi(loading = false, pair = pair(false, false), isOnline = true)),
        )
    }

    /** Offline there is nothing to stream, so the download is the only offer. */
    @Test
    fun `offline, a pair with nothing downloaded still offers the download`() {
        assertEquals(
            PrimaryAction.DownloadPair,
            primaryAction(BookDetailsUi(loading = false, pair = pair(false, false), isOnline = false)),
        )
    }

    /** Same rule for an unpaired audiobook: it streams too. */
    @Test
    fun `a standalone audiobook that is not downloaded offers to stream it`() {
        assertEquals(
            PrimaryAction.ListenStandalone,
            primaryAction(
                BookDetailsUi(loading = false, audiobook = audiobook(downloaded = false), isOnline = true),
            ),
        )
    }

    /**
     * **Not** symmetrical for an unpaired ebook. `StandaloneReaderScreen` has no
     * download shell — it assumes the caller checked — so an online ebook that
     * is not on the device must still offer the download, not the reader.
     */
    @Test
    fun `a standalone ebook that is not downloaded is never opened over the network`() {
        assertEquals(
            PrimaryAction.DownloadEbook,
            primaryAction(
                BookDetailsUi(loading = false, ebook = ebook(downloaded = false), isOnline = true),
            ),
        )
    }

    @Test
    fun `a download in progress shows no button`() {
        val ui = BookDetailsUi(
            loading = false,
            ebook = ebook(downloaded = false),
            downloadPercent = 42,
        )
        assertNull(primaryAction(ui))
    }

    @Test
    fun `an empty page shows no button`() {
        assertNull(primaryAction(BookDetailsUi(loading = false)))
    }
}
