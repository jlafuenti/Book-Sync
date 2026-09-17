package com.booksync.ui.tour

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.util.NetworkMonitor
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Which pair the walkthrough opens (issue #597 §3): the first synced pair
 * that already has something local, else any synced pair while online, else
 * nothing — at which point [TourController] replaces every `needsPair` step
 * with one skip card rather than silently dropping them.
 */
class TourPairPickerTest {

    private fun pair(
        id: Int,
        status: String = "synced",
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
    ) = BookPairEntity(
        id = id,
        ebookId = id,
        ebookTitle = "Book $id",
        ebookAuthor = null,
        ebookFilename = "book$id.epub",
        ebookFormat = "epub",
        audiobookId = id,
        audiobookTitle = "Book $id",
        audiobookAuthor = null,
        audiobookFilename = "book$id.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = status,
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
    )

    // ---- pure decision function ----

    @Test
    fun `prefers the first synced pair with something local`() {
        val pairs = listOf(
            pair(1, status = "synced"),
            pair(2, status = "synced", ebookDownloaded = true),
            pair(3, status = "synced", audiobookDownloaded = true),
        )
        assertEquals(2, choose(pairs, isOnline = true))
    }

    @Test
    fun `falls back to any synced pair while online when nothing is local`() {
        val pairs = listOf(pair(1, status = "auto_matched"), pair(2, status = "synced"))
        assertEquals(2, choose(pairs, isOnline = true))
    }

    @Test
    fun `offline with nothing local returns null even if a synced pair exists`() {
        val pairs = listOf(pair(1, status = "synced"))
        assertNull(choose(pairs, isOnline = false))
    }

    @Test
    fun `offline with a local synced pair still returns it`() {
        val pairs = listOf(pair(1, status = "synced", ebookDownloaded = true))
        assertEquals(1, choose(pairs, isOnline = false))
    }

    @Test
    fun `no synced pair at all returns null`() {
        val pairs = listOf(pair(1, status = "transcribing"), pair(2, status = "error"))
        assertNull(choose(pairs, isOnline = true))
    }

    @Test
    fun `empty library returns null`() {
        assertNull(choose(emptyList(), isOnline = true))
    }

    // ---- wiring through the DAO and NetworkMonitor ----

    @Test
    fun `pick reads pairs from the dao and online state from the network monitor`() = runBlocking {
        val dao = mockk<BookPairDao>()
        val networkMonitor = mockk<NetworkMonitor>()
        every { dao.getAllPairs() } returns flowOf(listOf(pair(7, status = "synced", ebookDownloaded = true)))
        every { networkMonitor.isOnline } returns MutableStateFlow(true)

        val picker = TourPairPicker(dao, networkMonitor)

        assertEquals(7, picker.pick())
    }
}
