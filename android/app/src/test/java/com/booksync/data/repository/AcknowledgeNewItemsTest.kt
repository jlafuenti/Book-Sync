package com.booksync.data.repository

import com.booksync.data.local.dao.AcknowledgedItemDao
import com.booksync.data.local.entity.AcknowledgedItemEntity
import com.booksync.data.remote.AcknowledgeItemsRequest
import com.booksync.data.remote.AcknowledgePairsRequest
import com.booksync.data.remote.AcknowledgeResponse
import com.booksync.data.remote.AudioBookResponse
import com.booksync.data.remote.BookPairResponse
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.EBookResponse
import com.booksync.data.remote.PageResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * Issue #222: the phone's NEW/acknowledged state used to be purely device-local
 * — a Room table the server never saw and that the server's own `acknowledged`
 * flag never seeded. Acknowledging on the web left the phone insisting
 * everything was new, and "Acknowledge all" on the phone left the web
 * unchanged.
 *
 * The contract pinned here:
 *
 * - acknowledging writes the local row **first** (so the badge clears instantly
 *   and offline still works) and then pushes to the server;
 * - a failed push is swallowed: the local row stands and nothing throws, so a
 *   dismiss tap never surfaces an error or rolls the UI back (P3 — no retry
 *   queue);
 * - a library refresh **seeds** the local table from the server's flag, which
 *   is how an acknowledgement made on the web reaches the phone. Seeding only
 *   ever inserts, so a local acknowledgement the server hasn't heard about yet
 *   is never resurrected as new;
 * - the DTO field defaults to `true`, so a server too old to send it leaves the
 *   phone's own state alone rather than declaring the whole library new.
 */
class AcknowledgeNewItemsTest {

    private val api = mockk<BookSyncApi>()
    private val acknowledgedItemDao = mockk<AcknowledgedItemDao>(relaxed = true)

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = mockk(relaxed = true),
        pendingSyncDao = mockk(relaxed = true),
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = acknowledgedItemDao,
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true),
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun ebook(id: Int, acknowledged: Boolean = true) = EBookResponse(
        id = id, title = "E$id", filename = "e$id.epub", format = "epub",
        uploaded_at = "2026-01-01T00:00:00Z", acknowledged = acknowledged,
    )

    private fun audiobook(id: Int, acknowledged: Boolean = true) = AudioBookResponse(
        id = id, title = "A$id", filename = "a$id.m4b", format = "m4b",
        uploaded_at = "2026-01-01T00:00:00Z", acknowledged = acknowledged,
    )

    private fun pair(id: Int, acknowledged: Boolean) = BookPairResponse(
        id = id,
        ebook = ebook(id * 10),
        audiobook = audiobook(id * 10 + 1),
        status = "synced",
        acknowledged = acknowledged,
    )

    private fun <T> page(items: List<T>) =
        PageResponse(items = items, total = items.size, page = 1, limit = 500)

    // ============ Push ============

    @Test
    fun `acknowledging pairs writes local rows and pushes the ids once`() = runTest {
        val rows = slot<List<AcknowledgedItemEntity>>()
        coEvery { acknowledgedItemDao.acknowledge(capture(rows)) } returns Unit
        val sent = slot<AcknowledgePairsRequest>()
        coEvery { api.acknowledgeNewPairs(capture(sent)) } returns AcknowledgeResponse(acknowledged_pairs = 2)

        repository().acknowledgeItems(listOf(1, 2), "pair")

        assertEquals(
            listOf(
                AcknowledgedItemEntity(TEST_SCOPE, 1, "pair"),
                AcknowledgedItemEntity(TEST_SCOPE, 2, "pair"),
            ),
            rows.captured,
        )
        coVerify(exactly = 1) { api.acknowledgeNewPairs(any()) }
        assertEquals(listOf(1, 2), sent.captured.pair_ids)
    }

    @Test
    fun `acknowledging ebooks pushes them as ebook_ids`() = runTest {
        val sent = slot<AcknowledgeItemsRequest>()
        coEvery { api.acknowledgeNewItems(capture(sent)) } returns AcknowledgeResponse(acknowledged_ebooks = 1)

        repository().acknowledgeItems(listOf(5), "ebook")

        assertEquals(listOf(5), sent.captured.ebook_ids)
        assertTrue(sent.captured.audiobook_ids.isEmpty())
    }

    @Test
    fun `acknowledging audiobooks pushes them as audiobook_ids`() = runTest {
        val sent = slot<AcknowledgeItemsRequest>()
        coEvery { api.acknowledgeNewItems(capture(sent)) } returns AcknowledgeResponse(acknowledged_audiobooks = 1)

        repository().acknowledgeItems(listOf(6), "audiobook")

        assertEquals(listOf(6), sent.captured.audiobook_ids)
        assertTrue(sent.captured.ebook_ids.isEmpty())
    }

    @Test
    fun `a failed push keeps the local rows and does not throw`() = runTest {
        val rows = slot<List<AcknowledgedItemEntity>>()
        coEvery { acknowledgedItemDao.acknowledge(capture(rows)) } returns Unit
        coEvery { api.acknowledgeNewPairs(any()) } throws IOException("offline")

        repository().acknowledgeItems(listOf(1, 2), "pair")

        assertEquals(2, rows.captured.size)
        coVerify(exactly = 1) { acknowledgedItemDao.acknowledge(any()) }
    }

    @Test
    fun `acknowledging nothing touches neither Room nor the server`() = runTest {
        repository().acknowledgeItems(emptyList(), "pair")

        coVerify(exactly = 0) { acknowledgedItemDao.acknowledge(any()) }
        coVerify(exactly = 0) { api.acknowledgeNewPairs(any()) }
    }

    // ============ Seeding from a refresh ============

    @Test
    fun `refreshPairs seeds acknowledged pairs and leaves unacknowledged ones new`() = runTest {
        coEvery { api.getPairs(1, 500) } returns page(listOf(pair(7, true), pair(8, false)))
        val rows = slot<List<AcknowledgedItemEntity>>()
        coEvery { acknowledgedItemDao.acknowledge(capture(rows)) } returns Unit

        repository().refreshPairs()

        assertEquals(listOf(AcknowledgedItemEntity(TEST_SCOPE, 7, "pair")), rows.captured)
    }

    @Test
    fun `refreshEbooks seeds only the acknowledged ebooks`() = runTest {
        coEvery { api.getEbooks(1, 500) } returns
            page(listOf(ebook(3, acknowledged = true), ebook(4, acknowledged = false)))
        val rows = slot<List<AcknowledgedItemEntity>>()
        coEvery { acknowledgedItemDao.acknowledge(capture(rows)) } returns Unit

        repository().refreshEbooks()

        assertEquals(listOf(AcknowledgedItemEntity(TEST_SCOPE, 3, "ebook")), rows.captured)
    }

    @Test
    fun `refreshAudiobooks seeds only the acknowledged audiobooks`() = runTest {
        coEvery { api.getAudiobooks(1, 500) } returns
            page(listOf(audiobook(3, acknowledged = false), audiobook(4, acknowledged = true)))
        val rows = slot<List<AcknowledgedItemEntity>>()
        coEvery { acknowledgedItemDao.acknowledge(capture(rows)) } returns Unit

        repository().refreshAudiobooks()

        assertEquals(listOf(AcknowledgedItemEntity(TEST_SCOPE, 4, "audiobook")), rows.captured)
    }

    @Test
    fun `a refresh with nothing acknowledged writes no rows`() = runTest {
        coEvery { api.getPairs(1, 500) } returns page(listOf(pair(8, false)))

        repository().refreshPairs()

        coVerify(exactly = 0) { acknowledgedItemDao.acknowledge(any()) }
    }

    // ============ DTO compatibility with older servers ============

    @Test
    fun `a payload without the acknowledged field decodes as acknowledged`() {
        val json = Json { ignoreUnknownKeys = true }
        val ebookJson = """{"id":1,"title":"E","filename":"e.epub","format":"epub",""" +
            """"uploaded_at":"2026-01-01T00:00:00Z"}"""
        val audiobookJson = """{"id":2,"title":"A","filename":"a.m4b","format":"m4b",""" +
            """"uploaded_at":"2026-01-01T00:00:00Z"}"""
        val pairJson = """{"id":3,"ebook":$ebookJson,"audiobook":$audiobookJson,"status":"synced"}"""

        assertTrue(json.decodeFromString<EBookResponse>(ebookJson).acknowledged)
        assertTrue(json.decodeFromString<AudioBookResponse>(audiobookJson).acknowledged)
        assertTrue(json.decodeFromString<BookPairResponse>(pairJson).acknowledged)
    }

    @Test
    fun `an explicit false in the payload is honoured`() {
        val json = Json { ignoreUnknownKeys = true }
        val ebookJson = """{"id":1,"title":"E","filename":"e.epub","format":"epub",""" +
            """"uploaded_at":"2026-01-01T00:00:00Z","acknowledged":false}"""

        assertEquals(false, json.decodeFromString<EBookResponse>(ebookJson).acknowledged)
    }
}
