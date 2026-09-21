package com.booksync.ui.account

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The Account → Storage "Sync data" line (issue #678). Seen on the emulator as
 * "Sync data — 1 books, about under 1 MB": the plural was unconditional and
 * "about" was prefixed to a label that is already a bound.
 */
class SyncDataStorageTitleTest {

    private val mb = 1024L * 1024L

    @Test
    fun `nothing cached says so`() {
        assertEquals("Sync data — none", syncDataStorageTitle(pairCount = 0, approxBytes = 0))
    }

    @Test
    fun `one book is singular and a small cache reads as a bound`() {
        assertEquals("Sync data — 1 book, under 1 MB", syncDataStorageTitle(1, 200_000))
    }

    @Test
    fun `several books are plural and a larger cache is approximate`() {
        assertEquals("Sync data — 3 books, about 12 MB", syncDataStorageTitle(3, 12 * mb))
    }
}
