package com.booksync.player

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * Issue #171: the phone used to refuse to play anything that was not fully
 * downloaded, while the browser streamed the same book instantly. The decision
 * "downloaded file if it is there, otherwise the server's stream URL" lives
 * here, free of Android and DI types, so it can be pinned.
 */
class MediaSourceSelectorTest {

    @get:Rule val temp = TemporaryFolder()

    private fun downloadedFile(): File =
        temp.newFile("book.m4b").apply { writeBytes(byteArrayOf(1, 2, 3)) }

    // --- downloaded -> the local file -------------------------------------

    @Test
    fun `a downloaded audiobook plays from the local file`() {
        val file = downloadedFile()

        val source = MediaSourceSelector.select(file, "https://tandem.example.com", 1516)

        assertEquals(AudioSource.LocalFile(file), source)
    }

    @Test
    fun `the local file wins even with a perfectly good server URL`() {
        val file = downloadedFile()

        val source = MediaSourceSelector.select(file, "https://tandem.example.com", 1516)

        assertTrue(
            "A downloaded book must never go back to the network — that is the " +
                "whole point of downloading it.",
            source is AudioSource.LocalFile,
        )
    }

    // --- not downloaded -> the stream URL ---------------------------------

    @Test
    fun `a missing local file streams from the server`() {
        val notThere = File(temp.root, "never-downloaded.m4b")

        val source = MediaSourceSelector.select(notThere, "https://tandem.example.com", 1516)

        assertEquals(
            AudioSource.Stream("https://tandem.example.com/api/files/audiobook/1516"),
            source,
        )
    }

    @Test
    fun `a null local file streams from the server`() {
        // localAudioFile() returns null for a filename it refuses to resolve
        // (issue #177), and Room may not know the row at all (issue #338) —
        // neither is a reason to have nothing to play.
        val source = MediaSourceSelector.select(null, "https://tandem.example.com", 1516)

        assertEquals(
            AudioSource.Stream("https://tandem.example.com/api/files/audiobook/1516"),
            source,
        )
    }

    @Test
    fun `a directory where the file should be is not a downloaded book`() {
        val dir = temp.newFolder("book.m4b")

        val source = MediaSourceSelector.select(dir, "https://tandem.example.com", 1516)

        assertTrue(source is AudioSource.Stream)
    }

    @Test
    fun `a zero-length file is not a downloaded book`() {
        // A half-written download leaves an empty file behind; playing it is an
        // instant error, streaming is not.
        val empty = temp.newFile("partial.m4b")

        val source = MediaSourceSelector.select(empty, "https://tandem.example.com", 1516)

        assertTrue(source is AudioSource.Stream)
    }

    // --- no credentials in the URL ----------------------------------------

    @Test
    fun `the stream URL carries no token`() {
        val url = MediaSourceSelector.audiobookStreamUrl("https://tandem.example.com", 1516)!!

        assertTrue(
            "Media tokens belong in the Authorization header, never in a URL " +
                "that lands in logcat: $url",
            !url.contains("token") && !url.contains("?"),
        )
    }

    // --- trailing-slash handling matches retrofitBaseUrl -------------------

    @Test
    fun `trailing slashes are normalised the way retrofitBaseUrl normalises them`() {
        val expected = "https://host/api/files/audiobook/7"

        assertEquals(expected, MediaSourceSelector.audiobookStreamUrl("https://host", 7))
        assertEquals(expected, MediaSourceSelector.audiobookStreamUrl("https://host/", 7))
        assertEquals(expected, MediaSourceSelector.audiobookStreamUrl("https://host///", 7))
        assertEquals(expected, MediaSourceSelector.audiobookStreamUrl("  https://host/  ", 7))
    }

    @Test
    fun `a bare host gets https, like every other URL the app builds`() {
        assertEquals(
            "https://tandem.example.com/api/files/audiobook/7",
            MediaSourceSelector.audiobookStreamUrl("tandem.example.com", 7),
        )
    }

    @Test
    fun `a sub-path deployment keeps its path`() {
        // BaseUrlInterceptor only rewrites scheme, host and port, so a server
        // reverse-proxied under a sub-path has to be carried in the URL here.
        assertEquals(
            "https://host/tandem/api/files/audiobook/7",
            MediaSourceSelector.audiobookStreamUrl("https://host/tandem", 7),
        )
    }

    @Test
    fun `an explicit port survives`() {
        assertEquals(
            "http://203.0.113.5:8000/api/files/audiobook/7",
            MediaSourceSelector.audiobookStreamUrl("http://203.0.113.5:8000", 7),
        )
    }

    // --- nothing to play ---------------------------------------------------

    @Test
    fun `no server and no file means nothing to play, not a localhost placeholder`() {
        // retrofitBaseUrl falls back to UNCONFIGURED_BASE_URL so Retrofit can be
        // constructed; a player must not chase that placeholder instead.
        assertNull(MediaSourceSelector.audiobookStreamUrl("", 7))
        assertNull(MediaSourceSelector.audiobookStreamUrl("   ", 7))
        assertNull(MediaSourceSelector.audiobookStreamUrl("not a url", 7))
        assertNull(MediaSourceSelector.select(null, "", 7))
    }

    @Test
    fun `a non-positive audiobook id has no stream URL`() {
        assertNull(MediaSourceSelector.audiobookStreamUrl("https://host", 0))
        assertNull(MediaSourceSelector.audiobookStreamUrl("https://host", -1))
    }

    @Test
    fun `an unusable server URL still plays a downloaded file`() {
        val file = downloadedFile()

        assertEquals(AudioSource.LocalFile(file), MediaSourceSelector.select(file, "", 1516))
    }
}
