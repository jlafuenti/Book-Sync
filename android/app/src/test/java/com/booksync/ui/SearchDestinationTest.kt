package com.booksync.ui

import com.booksync.ui.library.SearchResultItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
 * NavController: pairs go to the pair reader, standalone media goes where
 * Library sends it (`onOpenDetails` for an ebook — there is still no standalone
 * reader — and the standalone player for an audiobook).
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
    fun `a pair opens the pair reader`() {
        val dest = Routes.searchDestination(item("pair_84", pairId = 84, isEbook = true, isAudiobook = true))
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
}
