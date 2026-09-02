package com.booksync.ui

import com.booksync.data.repository.PairOpenTarget
import com.booksync.ui.library.SearchResultItem
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Where a Search result opens (issue #119).
 *
 * Search used to hand *every* tap to the pair routes: a standalone ebook's id
 * went to `reader/{pairId}`, so tapping ebook 294 opened "pair 294" — a
 * different book, or none at all. The reader's ⋮ menu then offered Mark Complete
 * and Reset Progress against a pair that never resolved, and if a pair with that
 * id did exist, both would have acted on the wrong book.
 *
 * The routing decision is pulled out here so it can be asserted without a
 * NavController: a pair opens in whichever format the user was last consuming
 * (issue #220), and standalone media goes where Library sends it — details for
 * an ebook, the standalone player for an audiobook.
 *
 * A standalone ebook still opens its *details* screen rather than the reader,
 * even though a standalone reader exists now (issue #169): SearchResultItem
 * carries no downloaded flag, so search cannot tell whether there is a file to
 * open. See Routes.searchDestination.
 */
class SearchDestinationTest {

    private fun item(
        id: String,
        pairId: Int? = null,
        isEbook: Boolean = false,
        isAudiobook: Boolean = false,
    ) = SearchResultItem(
        id = id,
        title = "The Infernal Devices",
        author = "Cassandra Clare",
        series = null,
        seriesIndex = null,
        isEbook = isEbook,
        isAudiobook = isAudiobook,
        pairId = pairId,
    )

    @Test
    fun `a pair the user was listening to opens the player, not the reader`() {
        // Issue #220. Home and Library route a pair through resolvePairOpenTarget
        // so the format last consumed is the one that reopens; Search used to
        // ignore that and always open the reader. Because reader saves claim
        // `ebook` (docs/position-sync-contract.md, "Who may claim source"), that
        // single tap then flipped the book's routing to the ebook for every
        // future open from Home and Library — the user searched for their
        // audiobook and quietly lost it.
        val dest = Routes.searchDestination(
            item("pair_84", pairId = 84, isEbook = true, isAudiobook = true),
            PairOpenTarget.Player,
        )
        assertEquals("player/84", dest)
    }

    @Test
    fun `a pair the user was reading opens the reader`() {
        val dest = Routes.searchDestination(
            item("pair_84", pairId = 84, isEbook = true, isAudiobook = true),
            PairOpenTarget.Reader,
        )
        assertEquals("reader/84", dest)
    }

    @Test
    fun `a pair with nothing downloaded still opens the reader`() {
        // Details is what resolvePairOpenTarget returns when neither side is on
        // the device. Search has no details route for a pair, and the reader is
        // where it went before, so this is deliberately unchanged behaviour.
        val dest = Routes.searchDestination(
            item("pair_84", pairId = 84, isEbook = true, isAudiobook = true),
            PairOpenTarget.Details,
        )
        assertEquals("reader/84", dest)
    }

    @Test
    fun `a pair whose target could not be resolved opens the reader`() {
        // The resolve is a suspend call against Room; if it fails or has not
        // finished, routing must still go somewhere sensible rather than nowhere.
        val dest = Routes.searchDestination(
            item("pair_84", pairId = 84, isEbook = true, isAudiobook = true),
            null,
        )
        assertEquals("reader/84", dest)
    }

    @Test
    fun `a standalone ebook opens its details screen, never a pair reader`() {
        val dest = Routes.searchDestination(item("ebook_294", isEbook = true))
        assertEquals("book_details/ebook/294", dest)
    }

    @Test
    fun `a standalone audiobook opens the standalone player`() {
        val dest = Routes.searchDestination(item("audiobook_17", isAudiobook = true))
        assertEquals("player/standalone/17", dest)
    }

    @Test
    fun `an id that carries no number goes nowhere`() {
        assertNull(Routes.searchDestination(item("ebook_", isEbook = true)))
    }

    @Test
    fun `a result that is neither ebook nor audiobook goes nowhere`() {
        assertNull(Routes.searchDestination(item("mystery_5")))
    }

    // ------------------------------------------------------------------
    // Wiring. The routing above is a pure function with a default argument,
    // so every assertion in this file still passes if no caller ever supplies
    // the target — the fix would be present and inert. Four guards in this
    // repository have already been caught that way, so read the call sites.
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
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    @Test
    fun `search actually resolves the open target before routing`() {
        assertTrue(
            "SearchScreen must call resolvePairOpenTarget — without it every test " +
                "above still passes on the default argument and search keeps " +
                "forcing the reader (issue #220).",
            // `viewModel.` matters: SearchViewModel's own declaration of
            // resolvePairOpenTarget lives in this same file, so a bare
            // contains() would pass with the call site deleted.
            source("com/booksync/ui/library/SearchScreen.kt")
                .contains("viewModel.resolvePairOpenTarget"),
        )
    }

    @Test
    fun `the navigation call site passes the resolved target through`() {
        val nav = source("com/booksync/ui/BookSyncNavigation.kt")
        assertTrue(
            "BookSyncNavigation must pass the resolved target to searchDestination; " +
                "calling it with the item alone silently takes the default.",
            Regex("""searchDestination\(\s*item\s*,\s*pairTarget\s*\)""").containsMatchIn(nav),
        )
    }
}
