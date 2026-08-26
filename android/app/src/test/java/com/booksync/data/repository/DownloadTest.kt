package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.AudioBookDao
import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.EBookDao
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import java.io.File
import java.io.IOException
import java.io.InputStream
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Response

/**
 * The four download functions share one contract (issue #160): a non-2xx
 * response or an empty body THROWS and leaves the downloaded flag untouched;
 * a success streams the bytes to disk and only then flips the flag.
 *
 * `downloadStandaloneAudiobook` was hand-copied without the status check: a
 * 401/404/500 wrote nothing to disk but still flipped `isDownloaded = true`,
 * so the book appeared downloaded, every player control was a silent no-op,
 * and DownloadWorker (which skips already-flagged rows) never retried.
 * Delete-and-re-download was the only recovery.
 */
class DownloadTest {

    private val api = mockk<BookSyncApi>()
    private val bookPairDao = mockk<BookPairDao>(relaxed = true)
    private val eBookDao = mockk<EBookDao>(relaxed = true)
    private val audioBookDao = mockk<AudioBookDao>(relaxed = true)
    private val context = mockk<Context>(relaxed = true)

    private lateinit var filesDir: File

    @Before
    fun setUp() {
        filesDir = File(System.getProperty("java.io.tmpdir"), "download-test-${System.nanoTime()}")
        filesDir.mkdirs()
        every { context.filesDir } returns filesDir
    }

    @After
    fun tearDown() {
        filesDir.deleteRecursively()
    }

    private fun repository() = BookSyncRepository(
        api = api,
        bookPairDao = bookPairDao,
        eBookDao = eBookDao,
        audioBookDao = audioBookDao,
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = mockk(relaxed = true),
        pendingSyncDao = mockk(relaxed = true),
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = context,
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
    )

    private val pair = BookPairEntity(
        id = 1, ebookId = 2, ebookTitle = "E", ebookAuthor = null,
        ebookFilename = "book.epub", ebookFormat = "epub",
        audiobookId = 3, audiobookTitle = "A", audiobookAuthor = null,
        audiobookFilename = "book.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 60, status = "paired",
    )
    private val ebook = EBookEntity(
        id = 2, title = "E", author = null, filename = "solo.epub", fileSize = null,
        format = "epub", series = null, seriesIndex = null, uploadedAt = "2026-01-01",
    )
    private val audio = AudioBookEntity(
        id = 3, title = "A", author = null, filename = "solo.m4b", durationSeconds = 60,
        format = "m4b", series = null, seriesIndex = null, uploadedAt = "2026-01-01",
    )

    private fun notFound(): Response<ResponseBody> =
        Response.error(404, "not found".toResponseBody("text/plain".toMediaType()))

    private fun ok(bytes: ByteArray): Response<ResponseBody> =
        Response.success(bytes.toResponseBody("application/octet-stream".toMediaType()))

    // ============ non-2xx: throw, no flag, no file ============

    @Test
    fun `standalone audiobook 404 throws and leaves the flag untouched`() = runTest {
        // The regression this issue exists for: this entry point used to flip
        // the flag on ANY response.
        coEvery { api.downloadAudiobook(3) } returns notFound()

        val thrown = runCatching { repository().downloadStandaloneAudiobook(audio) }.isFailure

        assertTrue("a 404 must throw", thrown)
        coVerify(exactly = 0) { audioBookDao.setDownloaded(any(), any()) }
        assertFalse(File(filesDir, "audiobooks/solo.m4b").exists())
    }

    @Test
    fun `paired audiobook 404 throws and leaves the flag untouched`() = runTest {
        coEvery { api.downloadAudiobook(3) } returns notFound()
        assertTrue(runCatching { repository().downloadAudiobook(pair) }.isFailure)
        coVerify(exactly = 0) { bookPairDao.setAudiobookDownloaded(any(), any()) }
    }

    @Test
    fun `paired ebook 404 throws and leaves the flag untouched`() = runTest {
        coEvery { api.downloadEbook(2) } returns notFound()
        assertTrue(runCatching { repository().downloadEbook(pair) }.isFailure)
        coVerify(exactly = 0) { bookPairDao.setEbookDownloaded(any(), any()) }
    }

    @Test
    fun `standalone ebook 404 throws and leaves the flag untouched`() = runTest {
        coEvery { api.downloadEbook(2) } returns notFound()
        assertTrue(runCatching { repository().downloadStandaloneEbook(ebook) }.isFailure)
        coVerify(exactly = 0) { eBookDao.setDownloaded(any(), any()) }
    }

    // ============ empty body: throw, no flag ============

    @Test
    fun `standalone audiobook empty body throws and leaves the flag untouched`() = runTest {
        coEvery { api.downloadAudiobook(3) } returns Response.success<ResponseBody>(200, null)

        assertTrue(runCatching { repository().downloadStandaloneAudiobook(audio) }.isFailure)
        coVerify(exactly = 0) { audioBookDao.setDownloaded(any(), any()) }
    }

    // ============ success: bytes on disk, flag set, progress de-duped ============

    @Test
    fun `standalone audiobook success writes the file and flips the flag`() = runTest {
        val payload = ByteArray(64 * 1024) { it.toByte() }
        coEvery { api.downloadAudiobook(3) } returns ok(payload)

        val progress = mutableListOf<Int>()
        val file = repository().downloadStandaloneAudiobook(audio) { progress.add(it) }

        assertTrue(payload.contentEquals(file.readBytes()))
        coVerify(exactly = 1) { audioBookDao.setDownloaded(3, true) }
        assertEquals("progress ends at 100", 100, progress.last())
        assertEquals("progress values never repeat", progress.distinct(), progress)
        assertEquals("progress is monotonic", progress.sorted(), progress)
    }

    @Test
    fun `paired ebook success writes the file, flips the flag, de-dupes progress`() = runTest {
        // downloadEbook was the one copy WITHOUT the lastProgress de-dup — the
        // drift the shared helper removes.
        val payload = ByteArray(64 * 1024) { it.toByte() }
        coEvery { api.downloadEbook(2) } returns ok(payload)

        val progress = mutableListOf<Int>()
        val file = repository().downloadEbook(pair) { progress.add(it) }

        assertTrue(payload.contentEquals(file.readBytes()))
        coVerify(exactly = 1) { bookPairDao.setEbookDownloaded(1, true) }
        assertEquals(progress.distinct(), progress)
    }

    // ============ interrupted stream: nothing under the final name ============

    @Test
    fun `a stream that dies mid-copy leaves no file under the final name`() = runTest {
        val body = mockk<ResponseBody>(relaxed = true)
        every { body.contentLength() } returns 1_000_000L
        every { body.byteStream() } returns object : InputStream() {
            private var served = 0
            override fun read(): Int = throw IOException("connection reset")
            override fun read(b: ByteArray, off: Int, len: Int): Int {
                if (served >= 16 * 1024) throw IOException("connection reset")
                served += len
                return len
            }
        }
        coEvery { api.downloadAudiobook(3) } returns Response.success(body)

        assertTrue(runCatching { repository().downloadStandaloneAudiobook(audio) }.isFailure)

        assertFalse(
            "a truncated transfer must not exist under the real filename — " +
                "buildCastMediaItem and the player only check File.isFile",
            File(filesDir, "audiobooks/solo.m4b").exists(),
        )
        coVerify(exactly = 0) { audioBookDao.setDownloaded(any(), any()) }
    }
}
