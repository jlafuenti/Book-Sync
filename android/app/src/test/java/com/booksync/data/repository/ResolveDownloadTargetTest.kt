package com.booksync.data.repository

import com.booksync.data.local.dao.AudioBookDao
import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.EBookDao
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.AudioBookResponse
import com.booksync.data.remote.BookPairResponse
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.EBookResponse
import com.booksync.data.remote.PageResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import java.io.IOException
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * A download must not depend on the Room cache already knowing the book
 * (issue #338).
 *
 * Search results come from the server, so a fresh install can name an id the
 * local cache has never seen. `DownloadWorker` read Room and only Room, so the
 * tap ended in `Result.failure("Audiobook not found in DB")` within ~40 ms,
 * with no request ever issued and nothing shown to the user. Refreshing the
 * Library tab fixed it, which is not something a user can be expected to guess.
 *
 * These pin the resolution contract the worker and the search screen now share:
 * cache first, server second, and a *distinguishable* answer for "the server
 * doesn't have it" (null) versus "we couldn't ask" (throws, so the worker's
 * retry policy and the snackbar can tell the two apart).
 */
class ResolveDownloadTargetTest {

    private val api = mockk<BookSyncApi>()
    private val bookPairDao = mockk<BookPairDao>(relaxed = true)
    private val eBookDao = mockk<EBookDao>(relaxed = true)
    private val audioBookDao = mockk<AudioBookDao>(relaxed = true)

    private fun repository() = buildRepository(
        api = api,
        bookPairDao = bookPairDao,
        eBookDao = eBookDao,
        audioBookDao = audioBookDao,
    )

    private fun notFound() = HttpException(
        Response.error<Any>(404, "".toResponseBody("application/json".toMediaType())),
    )

    private val cachedAudiobook = AudioBookEntity(
        id = 1516, title = "Cached", author = null, filename = "cached.m4b",
        durationSeconds = 60, format = "m4b", series = null, seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00",
    )

    private fun audiobookResponse(id: Int = 1516) = AudioBookResponse(
        id = id, title = "Remote", author = "A", filename = "remote.m4b",
        file_size = 100, duration_seconds = 90, format = "m4b",
        series = "S", series_index = 1f, uploaded_at = "2026-01-01T00:00:00",
        cover_path = "cover.jpg",
    )

    private fun ebookResponse(id: Int = 42) = EBookResponse(
        id = id, title = "Remote", author = "A", filename = "remote.epub",
        file_size = 100, format = "epub", series = "S", series_index = 1f,
        uploaded_at = "2026-01-01T00:00:00",
    )

    // --- Audiobooks ---------------------------------------------------------

    @Test
    fun `a cached audiobook never touches the network`() = runTest {
        coEvery { audioBookDao.getAudioBookById(1516) } returns cachedAudiobook

        assertEquals(cachedAudiobook, repository().resolveAudiobookById(1516))

        coVerify(exactly = 0) { api.getAudiobook(any()) }
    }

    @Test
    fun `an audiobook missing locally is fetched and cached`() = runTest {
        coEvery { audioBookDao.getAudioBookById(1516) } returns null
        coEvery { api.getAudiobook(1516) } returns audiobookResponse()

        val resolved = repository().resolveAudiobookById(1516)

        assertEquals(1516, resolved?.id)
        assertEquals("Remote", resolved?.title)
        assertEquals("remote.m4b", resolved?.filename)
        assertEquals("cover.jpg", resolved?.coverFilename)
        assertEquals(false, resolved?.isDownloaded)

        // Cached, or the very next screen asks the server all over again.
        val saved = slot<List<AudioBookEntity>>()
        coVerify { audioBookDao.upsertAudioBooks(capture(saved)) }
        assertEquals(listOf(resolved), saved.captured)
    }

    @Test
    fun `an audiobook the server does not have resolves to null`() = runTest {
        coEvery { audioBookDao.getAudioBookById(1516) } returns null
        coEvery { api.getAudiobook(1516) } throws notFound()

        assertNull(repository().resolveAudiobookById(1516))

        coVerify(exactly = 0) { audioBookDao.upsertAudioBooks(any()) }
    }

    @Test
    fun `an unreachable server throws rather than reporting a missing book`() = runTest {
        // Null means "the server doesn't have it" and stops the download for
        // good. A connection failure must stay a throw so the worker's retry
        // policy still applies and the message names the network, not the book.
        coEvery { audioBookDao.getAudioBookById(1516) } returns null
        coEvery { api.getAudiobook(1516) } throws IOException("connection refused")

        val thrown = runCatching { repository().resolveAudiobookById(1516) }.exceptionOrNull()

        assertTrue("expected the IOException to propagate, got $thrown", thrown is IOException)
    }

    // --- Ebooks -------------------------------------------------------------

    @Test
    fun `an ebook missing locally is fetched and cached`() = runTest {
        coEvery { eBookDao.getEBookById(42) } returns null
        coEvery { api.getEbook(42) } returns ebookResponse()

        val resolved = repository().resolveEbookById(42)

        assertEquals(42, resolved?.id)
        assertEquals("remote.epub", resolved?.filename)
        assertEquals(false, resolved?.isDownloaded)

        val saved = slot<List<EBookEntity>>()
        coVerify { eBookDao.upsertEBooks(capture(saved)) }
        assertEquals(listOf(resolved), saved.captured)
    }

    @Test
    fun `an ebook the server does not have resolves to null`() = runTest {
        coEvery { eBookDao.getEBookById(42) } returns null
        coEvery { api.getEbook(42) } throws notFound()

        assertNull(repository().resolveEbookById(42))
    }

    // --- Pairs --------------------------------------------------------------

    @Test
    fun `a pair missing locally falls back to a library refresh`() = runTest {
        // There is no single-pair endpoint on the server, so the pair path
        // reuses the refresh every Library pull already runs.
        var cached: BookPairEntity? = null
        coEvery { bookPairDao.getPairById(7) } answers { cached }
        coEvery { bookPairDao.upsertPairs(any()) } answers {
            cached = firstArg<List<BookPairEntity>>().first()
        }
        coEvery { api.getPairs(1, any()) } returns PageResponse(
            items = listOf(
                BookPairResponse(
                    id = 7,
                    ebook = ebookResponse(),
                    audiobook = audiobookResponse(),
                    status = "synced",
                ),
            ),
            total = 1, page = 1, limit = 100,
        )

        val resolved = repository().resolvePairById(7)

        assertEquals(7, resolved?.id)
        assertEquals("Remote", resolved?.ebookTitle)
    }

    @Test
    fun `a pair the server does not have resolves to null`() = runTest {
        coEvery { bookPairDao.getPairById(7) } returns null
        coEvery { api.getPairs(1, any()) } returns PageResponse(
            items = emptyList(), total = 0, page = 1, limit = 100,
        )

        assertNull(repository().resolvePairById(7))
    }
}
