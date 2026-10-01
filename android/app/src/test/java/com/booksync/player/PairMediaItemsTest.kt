package com.booksync.player

import com.booksync.data.local.entity.BookPairEntity
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairMediaItemsTest {

    private fun pair() = BookPairEntity(
        id = 7, ebookId = 3, ebookTitle = "Axis Test", ebookAuthor = "A. Author",
        ebookFilename = "axis-test.epub", ebookFormat = "epub",
        audiobookId = 9, audiobookTitle = "Axis Test", audiobookAuthor = "A. Author",
        audiobookFilename = "axis-test.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 3600, status = "matched",
    )

    // Uri.parse is a null-returning stub on the JVM, so the uri step is
    // injected away (uriOf); source selection itself is MediaSourceSelectorTest's.
    @Test
    fun `media id is the pair id`() {
        val item = PairMediaItems.build(pair(), localFile = null, serverUrl = "https://tandem.example.com", uriOf = { null })
        assertEquals(MediaId.Pair(7).value, item?.mediaId)
    }

    @Test
    fun `metadata carries the audiobook title and author`() {
        val item = PairMediaItems.build(pair(), localFile = null, serverUrl = "https://tandem.example.com", uriOf = { null })!!
        assertEquals("Axis Test", item.mediaMetadata.title)
        assertEquals("A. Author", item.mediaMetadata.artist)
    }

    @Test
    fun `no local file and no server url yields no item`() {
        assertNull(PairMediaItems.build(pair(), localFile = File("missing.m4b"), serverUrl = ""))
    }
}
