package com.booksync.data.repository

import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import java.io.File
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * A hostile filename must not escape its directory (issue #177).
 *
 * `ebook.filename` arrives in the server's JSON and used to be joined onto
 * `filesDir` verbatim, so `"../evil.bin"` wrote outside `files/ebooks/`. With
 * `"../datastore/booksync_prefs.preferences_pb"` that is the token store
 * overwritten by a book body, and DataStore throws `CorruptionException` on the
 * next start — a crash loop until storage is cleared. The delete paths were
 * worse: `.delete()` on the joined path removes any file under `filesDir`.
 *
 * Asserts against the filesystem rather than the helper: the helper has its own
 * tests, and what matters here is that the repository actually uses it. Before
 * the fix the download lands at `tempDir/evil.bin` and these fail.
 */
class DownloadPathContainmentTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val api = mockk<BookSyncApi>()

    private fun repository(filesDir: File) = BookSyncRepository(
        api = api,
        bookPairDao = mockk(relaxed = true),
        eBookDao = mockk(relaxed = true),
        audioBookDao = mockk(relaxed = true),
        syncPointDao = mockk(relaxed = true),
        bookmarkDao = mockk(relaxed = true),
        pendingSyncDao = mockk(relaxed = true),
        userProgressDao = mockk(relaxed = true),
        acknowledgedItemDao = mockk(relaxed = true),
        bookmarkLogDao = mockk(relaxed = true),
        context = mockk(relaxed = true) { every { this@mockk.filesDir } returns filesDir },
        diagnosticLogger = mockk(relaxed = true),
        deviceIdManager = mockk<DeviceIdManager>(relaxed = true),
        json = Json { ignoreUnknownKeys = true },
        userScopeProvider = testScopeProvider(),
    )

    private fun pair(ebookFilename: String) = BookPairEntity(
        id = 1,
        ebookId = 7,
        ebookTitle = "Book",
        ebookAuthor = null,
        ebookFilename = ebookFilename,
        ebookFormat = "epub",
        audiobookId = 9,
        audiobookTitle = "Book",
        audiobookAuthor = null,
        audiobookFilename = "book.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = 1,
        status = "ready",
        ebookDownloaded = true,
        audiobookDownloaded = false,
    )

    private fun ebook(filename: String) = EBookEntity(
        id = 7,
        title = "Book",
        author = null,
        filename = filename,
        fileSize = 4L,
        format = "epub",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00Z",
        isDownloaded = false,
    )

    private fun body() = retrofit2.Response.success(
        "evil".toResponseBody("application/epub+zip".toMediaType())
    )

    @Test
    fun `a traversing filename writes nothing outside the ebooks directory`() = runTest {
        val filesDir = tmp.newFolder("files")
        coEvery { api.downloadEbook(any()) } returns body()

        runCatching { repository(filesDir).downloadEbook(pair("../evil.bin")) }

        assertFalse(
            "the download escaped files/ebooks and landed in filesDir",
            File(filesDir, "evil.bin").exists(),
        )
        assertFalse(
            "the download escaped filesDir entirely",
            File(filesDir.parentFile, "evil.bin").exists(),
        )
    }

    @Test
    fun `the token store cannot be overwritten by a book body`() = runTest {
        // The concrete production consequence: DataStore throws
        // CorruptionException on next start, and the app cannot launch.
        val filesDir = tmp.newFolder("files")
        val datastore = File(filesDir, "datastore").apply { mkdirs() }
        val tokens = File(datastore, "booksync_prefs.preferences_pb")
        tokens.writeText("REAL TOKENS")
        coEvery { api.downloadEbook(any()) } returns body()

        runCatching {
            repository(filesDir).downloadEbook(pair("../datastore/booksync_prefs.preferences_pb"))
        }

        assertTrue("the token store was clobbered", tokens.readText() == "REAL TOKENS")
    }

    @Test
    fun `deleting a standalone ebook cannot delete an arbitrary file`() = runTest {
        val filesDir = tmp.newFolder("files")
        val planted = File(filesDir, "evil.bin")
        planted.writeText("keep me")

        repository(filesDir).deleteStandaloneEbook(ebook("../evil.bin"))

        assertTrue("delete escaped files/ebooks", planted.exists())
    }

    @Test
    fun `an honest filename still downloads where it always did`() = runTest {
        // The identity-mapping property. If this breaks, every already-downloaded
        // book stops resolving — the real risk of this change is over-rejection.
        val filesDir = tmp.newFolder("files")
        coEvery { api.downloadEbook(any()) } returns body()

        repository(filesDir).downloadEbook(pair("The Mad Ship.epub"))

        assertTrue(
            "an ordinary filename must still land in files/ebooks",
            File(File(filesDir, "ebooks"), "The Mad Ship.epub").exists(),
        )
    }
}
