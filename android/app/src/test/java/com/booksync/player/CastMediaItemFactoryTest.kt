package com.booksync.player

import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * What the Cast receiver is told to fetch (issue #225).
 *
 * The receiver streams the phone's own copy from `LocalCastHttpServer`, so the
 * URL it is handed has to be exactly `http://<ip>:<port>/<token>/<encoded
 * filename>` — the server matches the token as the first path segment and the
 * filename as the second, after URL-decoding. This used to be assembled inside
 * `AudioPlayerService.buildCastMediaItem`, where the encoding rules and the
 * three refusal cases (no server, no filename, no downloaded file) were only
 * ever exercised with a real Chromecast.
 */
class CastMediaItemFactoryTest {

    private val address = CastServerAddress(ip = "192.0.2.2", port = 41234, pathToken = "abc123")

    private fun original(mediaId: String = "pair_7") = MediaItem.Builder()
        .setMediaId(mediaId)
        .setMediaMetadata(
            MediaMetadata.Builder().setTitle("A Title").setArtist("An Author").build()
        )
        .build()

    // --- URL shape ---

    @Test
    fun `the stream URL is host, port, token, then the encoded filename`() {
        assertEquals(
            "http://192.0.2.2:41234/abc123/book.m4b",
            CastMediaItemFactory.streamUrl(address, "book.m4b"),
        )
    }

    @Test
    fun `spaces and reserved characters in the filename are percent-encoded, the token is not`() {
        assertEquals(
            "http://192.0.2.2:41234/abc123/The%20Book%20%231%20%26%20more.mp3",
            CastMediaItemFactory.streamUrl(address, "The Book #1 & more.mp3"),
        )
    }

    // --- Encoding: must match android.net.Uri.encode, which the receiver's
    // decoder was tuned against and which is a stub on the JVM ---

    @Test
    fun `unreserved characters survive encoding untouched`() {
        val unreserved = "AZaz09_-!.~'()*"
        assertEquals(unreserved, CastMediaItemFactory.encodePathSegment(unreserved))
    }

    @Test
    fun `reserved characters become uppercase percent escapes`() {
        assertEquals("%20", CastMediaItemFactory.encodePathSegment(" "))
        assertEquals("%2F", CastMediaItemFactory.encodePathSegment("/"))
        assertEquals("%25", CastMediaItemFactory.encodePathSegment("%"))
        assertEquals("%3F", CastMediaItemFactory.encodePathSegment("?"))
        assertEquals("%2B", CastMediaItemFactory.encodePathSegment("+"))
        assertEquals("%2C", CastMediaItemFactory.encodePathSegment(","))
    }

    @Test
    fun `non-ASCII is encoded as UTF-8 bytes`() {
        assertEquals("caf%C3%A9", CastMediaItemFactory.encodePathSegment("café"))
        assertEquals("%E6%9C%AC", CastMediaItemFactory.encodePathSegment("本"))
    }

    // --- MIME type ---

    @Test
    fun `the mime type follows the file extension, case-insensitively`() {
        assertEquals(MimeTypes.AUDIO_MPEG, CastMediaItemFactory.mimeTypeFor("a.mp3"))
        assertEquals(MimeTypes.AUDIO_MP4, CastMediaItemFactory.mimeTypeFor("a.m4b"))
        assertEquals(MimeTypes.AUDIO_MP4, CastMediaItemFactory.mimeTypeFor("a.M4A"))
        assertEquals(MimeTypes.AUDIO_FLAC, CastMediaItemFactory.mimeTypeFor("a.flac"))
        assertEquals(MimeTypes.AUDIO_OGG, CastMediaItemFactory.mimeTypeFor("a.ogg"))
        assertEquals(MimeTypes.AUDIO_AAC, CastMediaItemFactory.mimeTypeFor("a.aac"))
        assertEquals("audio/wav", CastMediaItemFactory.mimeTypeFor("a.wav"))
    }

    @Test
    fun `an unknown or missing extension falls back to audio mpeg`() {
        assertEquals(MimeTypes.AUDIO_MPEG, CastMediaItemFactory.mimeTypeFor("a.opus"))
        assertEquals(MimeTypes.AUDIO_MPEG, CastMediaItemFactory.mimeTypeFor("noextension"))
    }

    // --- The item ---

    @Test
    fun `no running server means nothing to load`() {
        assertNull(CastMediaItemFactory.build(original(), address = null, filename = "a.mp3", hasLocalFile = true))
    }

    @Test
    fun `an unresolvable book means nothing to load`() {
        assertNull(CastMediaItemFactory.build(original(), address, filename = null, hasLocalFile = true))
    }

    @Test
    fun `a book that is not on the phone is refused rather than pointed at the server`() {
        // The receiver cannot send a Bearer header, so a stream-only book would
        // earn a 401 and an idle television (issue #171).
        assertNull(CastMediaItemFactory.build(original(), address, filename = "a.mp3", hasLocalFile = false))
    }

    @Test
    fun `a downloaded book keeps its media id and metadata on the cast item`() {
        val item = CastMediaItemFactory.build(original("audiobook_9"), address, "a.mp3", hasLocalFile = true)
        assertNotNull(item)
        assertEquals("audiobook_9", item!!.mediaId)
        assertEquals("A Title", item.mediaMetadata.title.toString())
        assertEquals("An Author", item.mediaMetadata.artist.toString())
        // Artwork is deliberately dropped: the receiver runs in a browser and
        // cannot fetch a content:// URI.
        assertNull(item.mediaMetadata.artworkUri)
    }
}
