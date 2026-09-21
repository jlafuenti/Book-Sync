package com.booksync.data.repository

import com.booksync.data.local.entity.BookPairEntity
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The pure decision behind pruning cached sync maps for streamed books
 * (issue #678): on every library refresh, a cached map is dropped once
 * nothing is downloaded and the pair is not currently open (the reader or
 * the player/Android Auto — [SyncMapInUse]).
 */
class SyncMapPruningTest {

    private fun pair(
        id: Int = 1,
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
        syncMapDownloaded: Boolean = false,
    ) = BookPairEntity(
        id = id,
        ebookId = 7,
        ebookTitle = "Axis Test",
        ebookAuthor = "Author",
        ebookFilename = "axis.epub",
        ebookFormat = "epub",
        audiobookId = 9,
        audiobookTitle = "Axis Test",
        audiobookAuthor = "Author",
        audiobookFilename = "axis.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = 1_000,
        status = "synced",
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
        syncMapDownloaded = syncMapDownloaded,
    )

    @Test
    fun `a streamed pair's cached map is pruned when it is not in use`() {
        val streamed = pair(id = 1, syncMapDownloaded = true)

        val result = SyncMapPruning.pairsToPrune(listOf(streamed), inUsePairIds = emptySet())

        assertEquals(listOf(1), result)
    }

    @Test
    fun `a streamed pair's cached map is kept while it is registered in use`() {
        val streamed = pair(id = 1, syncMapDownloaded = true)

        val result = SyncMapPruning.pairsToPrune(listOf(streamed), inUsePairIds = setOf(1))

        assertEquals(emptyList<Int>(), result)
    }

    @Test
    fun `a downloaded pair's cached map is never pruned, in use or not`() {
        val downloaded = pair(id = 1, ebookDownloaded = true, syncMapDownloaded = true)

        assertEquals(emptyList<Int>(), SyncMapPruning.pairsToPrune(listOf(downloaded), inUsePairIds = emptySet()))
        assertEquals(emptyList<Int>(), SyncMapPruning.pairsToPrune(listOf(downloaded), inUsePairIds = setOf(1)))
    }

    @Test
    fun `a pair with no cached map is never a candidate`() {
        val nothingCached = pair(id = 1, syncMapDownloaded = false)

        assertEquals(emptyList<Int>(), SyncMapPruning.pairsToPrune(listOf(nothingCached), inUsePairIds = emptySet()))
    }

    @Test
    fun `pairsToPrune returns nothing for an empty list`() {
        assertEquals(emptyList<Int>(), SyncMapPruning.pairsToPrune(emptyList(), inUsePairIds = emptySet()))
    }

    @Test
    fun `a mixed library only prunes the streamed, unused pair`() {
        val streamedUnused = pair(id = 1, syncMapDownloaded = true)
        val streamedInUse = pair(id = 2, syncMapDownloaded = true)
        val downloaded = pair(id = 3, audiobookDownloaded = true, syncMapDownloaded = true)
        val neverCached = pair(id = 4)

        val result = SyncMapPruning.pairsToPrune(
            listOf(streamedUnused, streamedInUse, downloaded, neverCached),
            inUsePairIds = setOf(2),
        )

        assertEquals(listOf(1), result)
    }

    /**
     * Points on disk with the flag down — what a fetch cancelled between the
     * two writes left behind before that write was made atomic, and what
     * older builds may still carry. The flag alone never finds them, so the
     * prune also takes the pairs that actually hold points (issue #678).
     */
    @Test
    fun `orphaned points for a streamed pair are pruned even with the flag down`() {
        val streamed = pair(id = 1, syncMapDownloaded = false)

        val result = SyncMapPruning.pairsToPrune(
            listOf(streamed), inUsePairIds = emptySet(), pairIdsWithPoints = setOf(1),
        )

        assertEquals(listOf(1), result)
    }

    @Test
    fun `orphaned points are kept while the pair is in use or downloaded`() {
        val open = pair(id = 1)
        val downloaded = pair(id = 2, ebookDownloaded = true)

        val result = SyncMapPruning.pairsToPrune(
            listOf(open, downloaded), inUsePairIds = setOf(1), pairIdsWithPoints = setOf(1, 2),
        )

        assertEquals(emptyList<Int>(), result)
    }
}
