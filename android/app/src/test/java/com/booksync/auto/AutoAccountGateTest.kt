package com.booksync.auto

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Android Auto with no account signed in (issue #573).
 *
 * The behaviour under test is the **gate**, not the message. Before this, the
 * signed-out string was reachable only through `autoEmptyMessage`, wired in as
 * the empty-list fallback — so it was pinned by a test that asserted it given an
 * empty list, and the one state it had to cover (a populated cache, no account)
 * was the one state it could never reach. Every assertion below therefore hands
 * the gate a **full** library and asserts it is refused anyway.
 *
 * `Uri` and `Bundle` are JVM stubs here (`unitTests.isReturnDefaultValues =
 * true`), so items are asserted on media id, title and the browsable/playable
 * flags — the same constraint [AutoBrowseTreeTest] documents.
 */
class AutoAccountGateTest {

    private fun pair(id: Int, title: String) = AutoBook(
        mediaId = "pair_$id",
        title = title,
        author = "Author $id",
        audiobookId = id,
        pairId = id,
        resumePositionMs = 90_000L,
    )

    private val wholeLibrary = listOf(
        pair(1, "Bartleby"),
        pair(2, "Moby-Dick"),
        pair(3, "The Axis Test"),
    )

    /** A downloaded book plays from a local file; an undownloaded one streams. */
    private val downloadedUrl: (AutoBook) -> String? = { "file:///data/audiobooks/${it.audiobookId}.m4b" }
    private val streamedUrl: (AutoBook) -> String? = { "https://tandem.example.com/audio/${it.audiobookId}" }

    // --- What counts as having an account ---

    @Test
    fun `no token means no account`() {
        assertFalse(autoHasAccount(null))
    }

    @Test
    fun `a blank token means no account`() {
        // DataStore hands back "" rather than null in some clear paths; an empty
        // string is not a session.
        assertFalse(autoHasAccount(""))
        assertFalse(autoHasAccount("   "))
    }

    @Test
    fun `a token means an account`() {
        assertTrue(autoHasAccount("header.payload.signature"))
    }

    // --- Browse ---

    @Test
    fun `signed out, a browse node is the sign-in leaf and nothing else`() = runBlocking {
        val items = autoGatedBrowse(hasAccount = false) {
            autoBrowseItems(wholeLibrary, AUTO_EMPTY_LIBRARY_MESSAGE, downloadedUrl)
        }
        assertEquals(1, items.size)
        val leaf = items.single()
        assertEquals(AUTO_MESSAGE_ID, leaf.mediaId)
        assertEquals(AUTO_SIGNED_OUT_MESSAGE, leaf.mediaMetadata.title.toString())
        assertEquals(
            "The leaf must not be playable — it is the one row a signed-out " +
                "driver can see, and tapping it must do nothing.",
            false,
            leaf.mediaMetadata.isPlayable,
        )
        assertEquals(
            "…nor browsable: there is nothing underneath it.",
            false,
            leaf.mediaMetadata.isBrowsable,
        )
    }

    @Test
    fun `signed out, the cache is never read`() = runBlocking {
        var loaded = false
        autoGatedBrowse(hasAccount = false) {
            loaded = true
            autoBrowseItems(wholeLibrary, AUTO_EMPTY_LIBRARY_MESSAGE, downloadedUrl)
        }
        assertFalse(
            "The loader reads Room and fetches cover art over the network. " +
                "Signed out it must not run at all — the previous account's " +
                "library is not merely unrendered, it is unread (issue #573).",
            loaded,
        )
    }

    @Test
    fun `signed in, the node is whatever the loader built`() = runBlocking {
        val items = autoGatedBrowse(hasAccount = true) {
            autoBrowseItems(wholeLibrary, AUTO_EMPTY_LIBRARY_MESSAGE, downloadedUrl)
        }
        assertEquals(listOf("pair_1", "pair_2", "pair_3"), items.map { it.mediaId })
        assertTrue(items.all { it.mediaMetadata.isPlayable == true })
    }

    @Test
    fun `signed in with an empty library still gets its own message`() = runBlocking {
        val items = autoGatedBrowse(hasAccount = true) {
            autoBrowseItems(emptyList(), AUTO_EMPTY_LIBRARY_MESSAGE, downloadedUrl)
        }
        assertEquals(
            "\"you are signed out\" and \"you have no books\" are different " +
                "problems; the gate must not flatten them into one.",
            AUTO_EMPTY_LIBRARY_MESSAGE,
            items.single().mediaMetadata.title.toString(),
        )
    }

    // --- Search ---

    @Test
    fun `signed out, search returns nothing playable`() = runBlocking {
        var loaded = false
        val results = autoGatedSearch(hasAccount = false) {
            loaded = true
            autoSearch("bartleby", wholeLibrary.map { it.asSearchable() })
        }
        assertTrue(
            "A voice query must not resolve out of the cached search index — " +
                "that is how MEDIA_PLAY_FROM_SEARCH started the previous " +
                "account's book (issue #573).",
            results.isEmpty(),
        )
        assertFalse("…and the index must not even be built.", loaded)
    }

    @Test
    fun `signed out, search returns no message row either`() = runBlocking {
        val results: List<Any> = autoGatedSearch(hasAccount = false) {
            listOf(autoMessageItem(AUTO_SIGNED_OUT_MESSAGE))
        }
        assertTrue(
            "A row among search results reads as a hit — Assistant would try " +
                "to play it. Search says nothing; browse carries the message.",
            results.isEmpty(),
        )
    }

    @Test
    fun `signed in, search is whatever the matcher ranked`() = runBlocking {
        val results = autoGatedSearch(hasAccount = true) {
            autoSearch("bartleby", wholeLibrary.map { it.asSearchable() })
        }
        assertEquals(listOf("pair_1"), results.map { it.mediaId })
    }

    @Test
    fun `signed out, even a blank query resolves to nothing`() = runBlocking {
        // A blank query is Assistant's "play Tandem", which normally resumes the
        // most recent book — the one entry point that needs no title at all.
        val results = autoGatedSearch(hasAccount = false) {
            autoSearch("", wholeLibrary.map { it.asSearchable() })
        }
        assertTrue(results.isEmpty())
    }

    // --- Playback ---

    @Test
    fun `signed out, a known-good media id will not resolve - downloaded`() = runBlocking {
        var resolved = false
        val item = autoGatedPlayback(hasAccount = false) {
            resolved = true
            autoBookItem(wholeLibrary.first(), downloadedUrl(wholeLibrary.first())!!, null)
        }
        assertNull(
            "A file already on disk needs no token, so nothing downstream can " +
                "refuse it — this gate is the only thing between a signed-out " +
                "car and the previous account's audio (issue #573).",
            item,
        )
        assertFalse(resolved)
    }

    @Test
    fun `signed out, a known-good media id will not resolve - streamed`() = runBlocking {
        val item = autoGatedPlayback(hasAccount = false) {
            autoBookItem(wholeLibrary.first(), streamedUrl(wholeLibrary.first())!!, null)
        }
        assertNull(
            "The streamed case fails downstream on a 401 anyway, but it fails " +
                "as the host's generic \"Source error\" — refusing here is what " +
                "makes the reason knowable.",
            item,
        )
    }

    @Test
    fun `signed in, a media id resolves normally`() = runBlocking {
        val book = wholeLibrary.first()
        val built = autoBookItem(book, downloadedUrl(book)!!, null)
        val item = autoGatedPlayback(hasAccount = true) { built }
        assertSame(built, item)
    }

    @Test
    fun `signed in, an unresolvable media id is still null`() = runBlocking {
        // The gate must not turn "no such book" into something playable.
        assertNull(autoGatedPlayback<String>(hasAccount = true) { null })
    }
}
