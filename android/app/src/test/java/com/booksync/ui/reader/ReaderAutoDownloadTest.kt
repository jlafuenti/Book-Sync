package com.booksync.ui.reader

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #171: opening a paired ebook that was not on the device stopped at an
 * "Ebook Not Downloaded" prompt with a button, while the browser just opened
 * the book. An EPUB is small — the fix is to fetch it and open, not to ask.
 *
 * The rule is separate from the ViewModel because the ViewModel reaches this
 * decision from `getPairsFlow()`, which re-emits on every library refresh: the
 * dangerous version of "auto-download" is one that enqueues again on each
 * emission.
 */
class ReaderAutoDownloadTest {

    @Test
    fun `an undownloaded ebook is fetched without being asked for`() {
        assertTrue(shouldAutoDownloadEbook(ebookDownloaded = false, alreadyRequested = false))
    }

    @Test
    fun `a downloaded ebook is not re-fetched`() {
        assertFalse(shouldAutoDownloadEbook(ebookDownloaded = true, alreadyRequested = false))
    }

    @Test
    fun `the fetch is requested once, not once per library refresh`() {
        assertFalse(shouldAutoDownloadEbook(ebookDownloaded = false, alreadyRequested = true))
    }

    @Test
    fun `a cancelled fetch is not silently restarted`() {
        // Cancel sets alreadyRequested and leaves it set: the user said no, and
        // the very next Room emission must not override that. The visible
        // "Download Ebook" button is how they change their mind.
        assertFalse(shouldAutoDownloadEbook(ebookDownloaded = false, alreadyRequested = true))
    }
}
