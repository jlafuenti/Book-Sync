package com.booksync.auto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android Auto browse tree (issue #172).
 *
 * The shape of this tree is what the car app quality review actually looks at —
 * root tabs, depth, how many nodes a level can hold, and what the driver sees
 * when there is nothing to show. It used to live inside
 * [com.booksync.player.AudioPlayerService], which is excluded from Kover as
 * framework glue, so none of it was pinned by anything.
 *
 * Note on the assertions: `Uri` and `Bundle` are JVM stubs in unit tests
 * (`unitTests.isReturnDefaultValues = true`), so a built `MediaItem` can be
 * asserted on its media id, title, artist and browsable/playable flags but not
 * on its URI or extras. That is why [autoBrowseItems] takes the source URL as a
 * *string* and decides inclusion from that: "is this book playable at all" is
 * then a pure decision this test can hold.
 */
class AutoBrowseTreeTest {

    private fun pair(id: Int, title: String, audiobookId: Int = id) = AutoBook(
        mediaId = "pair_$id",
        title = title,
        author = "Author $id",
        audiobookId = audiobookId,
        pairId = id,
    )

    private fun standalone(id: Int, title: String) = AutoBook(
        mediaId = "audiobook_$id",
        title = title,
        author = "Author $id",
        audiobookId = id,
    )

    /** Every book has a source: downloaded file or, since issue #171, a stream URL. */
    private val everythingPlayable: (AutoBook) -> String? = { "https://example.test/${it.audiobookId}" }

    // --- Root ---

    @Test
    fun `the root has exactly the two tabs, both browsable and neither playable`() {
        val tabs = autoRootTabs()
        assertEquals(listOf(AUTO_TAB_CONTINUE, AUTO_TAB_LIBRARY), tabs.map { it.mediaId })
        assertEquals(listOf("Continue Listening", "Library"), tabs.map { it.mediaMetadata.title.toString() })
        assertTrue(tabs.all { it.mediaMetadata.isBrowsable == true })
        assertTrue(tabs.all { it.mediaMetadata.isPlayable == false })
    }

    @Test
    fun `the tree is two levels deep - a tab's children are leaves`() {
        val leaves = autoBrowseItems(listOf(pair(1, "A")), AUTO_EMPTY_LIBRARY_MESSAGE, everythingPlayable)
        assertTrue(
            "Books must be leaves. A browsable book would add a third level and " +
                "the car checklist caps browse depth.",
            leaves.all { it.mediaMetadata.isBrowsable == false && it.mediaMetadata.isPlayable == true },
        )
    }

    // --- Continue Listening ---

    @Test
    fun `continue listening keeps the recency order it was given, pairs before standalone`() {
        val books = continueListeningBooks(
            pairs = listOf(pair(3, "Third"), pair(1, "First")),
            standalone = listOf(standalone(7, "Seventh")),
        )
        assertEquals(listOf("pair_3", "pair_1", "audiobook_7"), books.map { it.mediaId })
    }

    @Test
    fun `continue listening carries the resume position through`() {
        val books = continueListeningBooks(
            pairs = listOf(pair(1, "First").copy(resumePositionMs = 42_000L)),
            standalone = emptyList(),
        )
        assertEquals(42_000L, books.single().resumePositionMs)
    }

    // --- Library ---

    @Test
    fun `the library sorts alphabetically, ignoring case, across pairs and standalone`() {
        val books = libraryBooks(
            pairs = listOf(pair(1, "zebra"), pair(2, "Apple")),
            standalone = listOf(standalone(30, "mango")),
        )
        assertEquals(listOf("Apple", "mango", "zebra"), books.map { it.title })
    }

    @Test
    fun `an audiobook already shown as a pair is not listed twice`() {
        val books = libraryBooks(
            pairs = listOf(pair(1, "Bartleby", audiobookId = 55)),
            standalone = listOf(standalone(55, "Bartleby"), standalone(56, "Moby-Dick")),
        )
        assertEquals(listOf("pair_1", "audiobook_56"), books.map { it.mediaId })
    }

    // --- Node counts ---

    @Test
    fun `each browse node stays under the car limit`() {
        val many = (1..500).map { standalone(it, "Book %03d".format(it)) }
        assertEquals(AUTO_MAX_ITEMS_PER_NODE, libraryBooks(emptyList(), many).size)
        assertEquals(AUTO_MAX_ITEMS_PER_NODE, continueListeningBooks(emptyList(), many).size)
        assertTrue("The cap must be a cap, not a formality", AUTO_MAX_ITEMS_PER_NODE <= 100)
    }

    // --- Playability ---

    @Test
    fun `a book that is not downloaded is still browsable now that streaming exists`() {
        // Before issue #171 a book with no local file had no URI and was dropped
        // from the tree entirely, so the car showed a shorter library than the
        // phone. The stream URL is built from the audiobook id, so it exists
        // whenever a server is configured.
        val items = autoBrowseItems(
            listOf(standalone(9, "Streamed Only")),
            AUTO_EMPTY_LIBRARY_MESSAGE,
            urlFor = { "https://example.test/stream/9" },
        )
        assertEquals(listOf("audiobook_9"), items.map { it.mediaId })
    }

    @Test
    fun `a book with no source at all is dropped rather than shown as a dead row`() {
        val items = autoBrowseItems(
            listOf(standalone(9, "Nowhere"), standalone(10, "Somewhere")),
            AUTO_EMPTY_LIBRARY_MESSAGE,
            urlFor = { if (it.audiobookId == 10) "https://example.test/10" else null },
        )
        assertEquals(listOf("audiobook_10"), items.map { it.mediaId })
    }

    @Test
    fun `titles and authors reach the media item`() {
        val items = autoBrowseItems(listOf(pair(1, "Bartleby")), AUTO_EMPTY_LIBRARY_MESSAGE, everythingPlayable)
        assertEquals("Bartleby", items.single().mediaMetadata.title.toString())
        assertEquals("Author 1", items.single().mediaMetadata.artist.toString())
    }

    // --- Empty / error state ---

    @Test
    fun `an empty node shows a message leaf instead of nothing at all`() {
        val items = autoBrowseItems(emptyList(), AUTO_SIGNED_OUT_MESSAGE, everythingPlayable)
        val only = items.single()
        assertEquals(AUTO_MESSAGE_ID, only.mediaId)
        assertEquals(AUTO_SIGNED_OUT_MESSAGE, only.mediaMetadata.title.toString())
    }

    @Test
    fun `the message leaf cannot be played or browsed into`() {
        val only = autoBrowseItems(emptyList(), AUTO_SIGNED_OUT_MESSAGE, everythingPlayable).single()
        assertFalse("Tapping the message must not try to start playback", only.mediaMetadata.isPlayable!!)
        assertFalse("The message is not a folder", only.mediaMetadata.isBrowsable!!)
    }

    @Test
    fun `search results get no message row - a browser draws its own no-results`() {
        // A message row among search results reads as a hit, and the driver
        // taps it. Browse nodes pass a message; search passes null.
        assertTrue(autoBrowseItems(emptyList(), null, everythingPlayable).isEmpty())
    }

    @Test
    fun `the signed-out message tells the driver what to do off the road`() {
        // Car checklist: a clear error state, not an empty list. Nothing in the
        // car can fix a missing sign-in, so the message points at the phone.
        assertTrue(AUTO_SIGNED_OUT_MESSAGE.contains("phone"))
    }

    @Test
    fun `every book node id is one the player can resolve`() {
        val items = autoBrowseItems(
            libraryBooks(listOf(pair(1, "A")), listOf(standalone(2, "B"))),
            AUTO_EMPTY_LIBRARY_MESSAGE,
            everythingPlayable,
        )
        assertTrue(
            "Only pair_N / audiobook_N ids exist — see MediaId (issue #141).",
            items.all { it.mediaId.startsWith("pair_") || it.mediaId.startsWith("audiobook_") },
        )
    }
}
