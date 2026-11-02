package com.booksync.auto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Voice search ranking (issue #172). Android Auto and Assistant hand the app a
 * spoken phrase, not a media id, so the whole feature is "turn a fuzzy string
 * into the one book the driver meant". That decision is pure and lives here
 * rather than in [com.booksync.player.AudioPlayerService] — which is excluded
 * from Kover as framework glue, and is a large part of why voice search was
 * declared in the manifest and never actually implemented.
 */
class AutoSearchTest {

    private val library = listOf(
        AutoSearchable("pair_1", "Bartleby, the Scrivener", "Herman Melville"),
        AutoSearchable("audiobook_2", "Moby-Dick", "Herman Melville"),
        AutoSearchable("pair_3", "The Fellowship of the Ring", "J. R. R. Tolkien", listOf("The Lord of the Rings")),
        AutoSearchable("audiobook_4", "The Two Towers", "J. R. R. Tolkien", listOf("The Lord of the Rings")),
        AutoSearchable("pair_5", "Bartleby and Benito Cereno", "Herman Melville"),
        AutoSearchable("audiobook_6", "Bartleby", "Herman Melville"),
    )

    private fun ids(query: String) = autoSearch(query, library).map { it.mediaId }

    @Test
    fun `an exact title wins over books that merely start with the query`() {
        assertEquals(listOf("audiobook_6", "pair_1", "pair_5"), ids("Bartleby"))
    }

    @Test
    fun `a title prefix matches`() {
        assertEquals(listOf("audiobook_2"), ids("Moby"))
    }

    @Test
    fun `a title match ranks above a series-only match`() {
        // "ring" is inside one book's title and inside the other's series.
        assertEquals(listOf("pair_3", "audiobook_4"), ids("ring"))
    }

    @Test
    fun `a partial title matches inside the title, not only from the start`() {
        assertEquals(listOf("pair_3"), ids("fellowship"))
    }

    @Test
    fun `an author name returns that author's books`() {
        assertEquals(
            setOf("pair_1", "audiobook_2", "pair_5", "audiobook_6"),
            ids("Herman Melville").toSet(),
        )
    }

    @Test
    fun `a series name returns the books in that series`() {
        assertEquals(setOf("pair_3", "audiobook_4"), ids("lord of the rings").toSet())
    }

    @Test
    fun `a book in two series is found by either one`() {
        val books = listOf(
            AutoSearchable("pair_1", "Axis Crossover", "An Author", listOf("Axis Saga", "Axis Chronological")),
            AutoSearchable("pair_2", "Axis Prequel", "An Author", listOf("Axis Chronological")),
            AutoSearchable("pair_3", "Elsewhere", "An Author", listOf("Another Series")),
        )

        assertEquals(listOf("pair_1"), autoSearch("axis saga", books).map { it.mediaId })
        assertEquals(setOf("pair_1", "pair_2"), autoSearch("axis chronological", books).map { it.mediaId }.toSet())
        assertEquals(setOf("pair_1", "pair_2"), autoSearch("chronolog", books).map { it.mediaId }.toSet())
    }

    @Test
    fun `an exact series match outranks a partial one on another series of the same book`() {
        val books = listOf(
            AutoSearchable("pair_1", "Book A", null, listOf("Axis Saga Extended")),
            AutoSearchable("pair_2", "Book B", null, listOf("Other", "Axis Saga")),
        )

        assertEquals(listOf("pair_2", "pair_1"), autoSearch("axis saga", books).map { it.mediaId })
    }

    @Test
    fun `matching ignores case and punctuation`() {
        // "moby dick" — no hyphen, lower case — must still find "Moby-Dick".
        assertEquals(listOf("audiobook_2"), ids("moby dick"))
    }

    @Test
    fun `no match returns nothing rather than the whole library`() {
        assertTrue(autoSearch("Dune", library).isEmpty())
    }

    @Test
    fun `a blank query returns the library in the order it was given`() {
        // "Hey Google, play Tandem" arrives as an empty query. The car checklist
        // wants that to start *something*, so the caller plays the first result —
        // which is why the service passes Continue Listening order in first.
        assertEquals(library.map { it.mediaId }, autoSearch("", library).map { it.mediaId })
        assertEquals(library.map { it.mediaId }, autoSearch("   ", library).map { it.mediaId })
    }

    @Test
    fun `results are capped so a browser never has to render an unbounded list`() {
        val many = (1..500).map { AutoSearchable("audiobook_$it", "Book $it", "Anon") }
        assertEquals(AUTO_MAX_SEARCH_RESULTS, autoSearch("book", many).size)
        assertEquals(AUTO_MAX_SEARCH_RESULTS, autoSearch("", many).size)
    }

    @Test
    fun `an empty library never matches`() {
        assertTrue(autoSearch("anything", emptyList()).isEmpty())
        assertTrue(autoSearch("", emptyList()).isEmpty())
    }

    @Test
    fun `a null author and series only match on title`() {
        val books = listOf(AutoSearchable("audiobook_9", "Ulysses", null, emptyList()))
        assertTrue(autoSearch("Joyce", books).isEmpty())
        assertEquals(listOf("audiobook_9"), autoSearch("ulysses", books).map { it.mediaId })
    }
}
