package com.booksync.ui.reader

import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Issue #730, task 11. Page counts are cached per ebook in
 * `filesDir/page_counts/<ebookId>.json`, at most five layouts per book.
 */
class PageCountCacheTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun cache(dir: File = tmp.root.resolve("page_counts")) = PageCountCache(dir)

    private val counts = Counts(counts = listOf(1, 20, 7), chars = listOf(30, 51000, 17000))

    @Test
    fun `write then read round-trips`() {
        val c = cache()
        val key = c.key(7, "3:abc", "--USER__fontSize: 100% !important;", 1080, 2000)
        c.write(7, key, counts)
        assertEquals(counts, c.read(7, key))
        assertTrue(tmp.root.resolve("page_counts/7.json").isFile)
    }

    @Test
    fun `a fresh instance reads what another wrote`() {
        val key = cache().key(7, "sig", "s", 1, 2)
        cache().write(7, key, counts)
        assertEquals(counts, cache().read(7, key))
    }

    @Test
    fun `missing book or missing key reads null`() {
        val c = cache()
        assertNull(c.read(1, "k"))
        c.write(1, "k", counts)
        assertNull(c.read(1, "other"))
        assertNull(c.read(2, "k"))
    }

    @Test
    fun `books do not share entries`() {
        val c = cache()
        c.write(1, "k", counts)
        val other = Counts(listOf(9), listOf(9))
        c.write(2, "k", other)
        assertEquals(counts, c.read(1, "k"))
        assertEquals(other, c.read(2, "k"))
    }

    @Test
    fun `rewriting a key replaces its counts`() {
        val c = cache()
        c.write(1, "k", counts)
        val newer = Counts(listOf(2, 2, 2), listOf(1, 1, 1))
        c.write(1, "k", newer)
        assertEquals(newer, c.read(1, "k"))
    }

    @Test
    fun `a sixth key evicts the oldest, keeping five`() {
        val c = cache()
        for (i in 1..6) c.write(1, "k$i", Counts(listOf(i), listOf(i)))
        assertNull(c.read(1, "k1"))
        for (i in 2..6) assertEquals(Counts(listOf(i), listOf(i)), c.read(1, "k$i"))
    }

    @Test
    fun `rewriting an old key makes it the newest, so it survives the next eviction`() {
        val c = cache()
        for (i in 1..5) c.write(1, "k$i", Counts(listOf(i), listOf(i)))
        c.write(1, "k1", Counts(listOf(10), listOf(10)))
        c.write(1, "k6", Counts(listOf(6), listOf(6)))
        assertEquals(Counts(listOf(10), listOf(10)), c.read(1, "k1"))
        assertNull(c.read(1, "k2"))
    }

    @Test
    fun `a corrupt file reads as null, and the next write replaces it`() {
        val dir = tmp.root.resolve("page_counts").apply { mkdirs() }
        dir.resolve("3.json").writeText("{ this is not json")
        val c = cache(dir)
        assertNull(c.read(3, "k"))
        c.write(3, "k", counts)
        assertEquals(counts, c.read(3, "k"))
    }

    @Test
    fun `a file with the wrong shape reads as null`() {
        val dir = tmp.root.resolve("page_counts").apply { mkdirs() }
        dir.resolve("3.json").writeText("""{"entries":[{"key":"k","counts":[1,2],"chars":[1]}]}""")
        assertNull(cache(dir).read(3, "k"))
    }

    @Test
    fun `key depends on every input`() {
        val c = cache()
        val base = c.key(1, "sig", "style", 100, 200)
        assertEquals(base, c.key(1, "sig", "style", 100, 200))
        assertNotEquals(base, c.key(2, "sig", "style", 100, 200))
        assertNotEquals(base, c.key(1, "sig2", "style", 100, 200))
        assertNotEquals(base, c.key(1, "sig", "style2", 100, 200))
        assertNotEquals(base, c.key(1, "sig", "style", 101, 200))
        assertNotEquals(base, c.key(1, "sig", "style", 100, 201))
    }

    @Test
    fun `key stays short however long the style is`() {
        val key = cache().key(1, "sig", "x".repeat(10_000), 100, 200)
        assertTrue(key.length < 100)
    }

    @Test
    fun `suspend variants read and write through the given dispatcher`() = runTest {
        val c = PageCountCache(tmp.root.resolve("page_counts"), StandardTestDispatcher(testScheduler))
        c.save(4, "k", counts)
        assertEquals(counts, c.load(4, "k"))
        assertNull(c.load(4, "missing"))
    }
}
