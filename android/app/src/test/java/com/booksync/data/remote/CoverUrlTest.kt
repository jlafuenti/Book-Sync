package com.booksync.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Issue #126: cover filenames come from the book's author/title, so they can
 * legally contain '#', spaces and other characters that are reserved in a URL.
 * Concatenating the raw cover_path onto the server URL let OkHttp (via Coil)
 * read the '#' as a fragment delimiter, truncating the request path -- the
 * cover 404'd.
 */
class CoverUrlTest {

    @Test
    fun `percent-encodes a hash so it is not parsed as a fragment`() {
        assertEquals(
            "https://tandem.example.com/api/files/covers/Private_%231_Suspect.jpg",
            coverImageUrl("https://tandem.example.com", "/api/files/covers/Private_#1_Suspect.jpg"),
        )
    }

    @Test
    fun `percent-encodes spaces alongside the hash`() {
        assertEquals(
            "https://tandem.example.com/api/files/covers/" +
                "James_Patterson%20-%20Private_%231_Suspect.jpg",
            coverImageUrl(
                "https://tandem.example.com",
                "/api/files/covers/James_Patterson - Private_#1_Suspect.jpg",
            ),
        )
    }

    @Test
    fun `leaves an ordinary filename byte-identical to the old concatenation`() {
        val serverUrl = "https://tandem.example.com"
        val coverPath = "/api/files/covers/audiobook_252.jpg"

        assertEquals(
            serverUrl.trimEnd('/') + coverPath,
            coverImageUrl(serverUrl, coverPath),
        )
    }

    @Test
    fun `tolerates a trailing slash on the server url`() {
        assertEquals(
            "https://tandem.example.com/api/files/covers/a.jpg",
            coverImageUrl("https://tandem.example.com/", "/api/files/covers/a.jpg"),
        )
    }

    @Test
    fun `keeps a subpath in the configured server url`() {
        assertEquals(
            "https://example.com/tandem/api/files/covers/a.jpg",
            coverImageUrl("https://example.com/tandem", "/api/files/covers/a.jpg"),
        )
    }

    @Test
    fun `returns null for an unusable server url so callers fall back to the placeholder`() {
        assertNull(coverImageUrl("", "/api/files/covers/a.jpg"))
        assertNull(coverImageUrl("not a url", "/api/files/covers/a.jpg"))
    }

    @Test
    fun `returns null for a blank cover path`() {
        assertNull(coverImageUrl("https://tandem.example.com", ""))
    }
}
