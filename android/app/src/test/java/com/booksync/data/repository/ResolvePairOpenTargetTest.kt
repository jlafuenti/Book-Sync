package com.booksync.data.repository

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [BookSyncRepository.resolvePairOpenTarget] had zero unit coverage (issue
 * #61/#40). It's the function that decides which format a tap on a pair
 * opens — it reads the local bookmark's `source`, which is exactly what
 * [PositionSavePolicy]'s `LocalMetadataOnly` verdict writes (via
 * `updateBookmarkMetadata`) even when the reader's restore never resolved a
 * real position. If this routing broke, a session saved through that path
 * would silently stop landing back in the reader.
 */
class ResolvePairOpenTargetTest {

    private val bookPairDao = mockk<BookPairDao>()
    private val bookmarkDao = mockk<BookmarkDao>()

    private fun repository() = buildRepository(
        bookPairDao = bookPairDao,
        bookmarkDao = bookmarkDao,
    )

    private fun pair(
        id: Int = 84,
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
    ) = BookPairEntity(
        id = id,
        ebookId = 1,
        ebookTitle = "Mad Ship",
        ebookAuthor = "Robin Hobb",
        ebookFilename = "mad-ship.epub",
        ebookFormat = "epub",
        audiobookId = 2,
        audiobookTitle = "Mad Ship",
        audiobookAuthor = "Robin Hobb",
        audiobookFilename = "mad-ship.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = "paired",
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
    )

    private fun bookmark(source: String) = BookmarkEntity(scopeKey = TEST_SCOPE, 
        bookPairId = 84,
        source = source,
        epubChapter = null,
        epubSentenceIndex = null,
        audioPositionMs = null,
        updatedAt = "1000",
    )

    @Test
    fun `source=ebook with ebook downloaded opens the Reader`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("ebook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = true), isOnline = false)

        assertEquals(PairOpenTarget.Reader, target)
    }

    @Test
    fun `source=audiobook with audiobook downloaded opens the Player`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("audiobook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = true), isOnline = false)

        assertEquals(PairOpenTarget.Player, target)
    }

    @Test
    fun `no bookmark falls back to whichever format is downloaded, preferring ebook`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns null

        assertEquals(
            PairOpenTarget.Reader,
            repository().resolvePairOpenTarget(
                pair(ebookDownloaded = true, audiobookDownloaded = true), isOnline = false))
        assertEquals(
            PairOpenTarget.Player,
            repository().resolvePairOpenTarget(
                pair(ebookDownloaded = false, audiobookDownloaded = true), isOnline = false))
        assertEquals(
            PairOpenTarget.Details,
            repository().resolvePairOpenTarget(
                pair(ebookDownloaded = false, audiobookDownloaded = false), isOnline = false))
    }

    @Test
    fun `offline, a source naming a format that is not downloaded falls back`() = runTest {
        // e.g. the audiobook was deleted locally after the last audio save, and
        // there is no connection to stream it over.
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("audiobook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = false), isOnline = false)

        assertEquals(PairOpenTarget.Reader, target)
    }

    @Test
    fun `a session saved via LocalMetadataOnly stamps source=ebook and still routes to the Reader`() = runTest {
        // PositionSavePolicy.LocalMetadataOnly (updateBookmarkMetadata) writes
        // ONLY source/updatedAt, leaving epubChapter/epubLocator/etc. null —
        // exactly what an unresolved restore looks like. Routing must not
        // depend on those anchors being populated.
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns BookmarkEntity(scopeKey = TEST_SCOPE, 
            bookPairId = 84,
            source = "ebook",
            epubChapter = null,
            epubSentenceIndex = null,
            audioPositionMs = null,
            epubLocator = null,
            updatedAt = "1000",
        )

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = true), isOnline = false)

        assertEquals(PairOpenTarget.Reader, target)
    }

    // ---- #484: openable is not the same as downloaded ----

    /**
     * The complaint this fixes: stream a book, come back, tap its cover, and
     * land in the *reader*. `source` said audiobook and the claim was checked
     * against `audiobookDownloaded`, which a streamed book never satisfies, so
     * the claim was skipped and the ladder fell through to the ebook.
     */
    @Test
    fun `online, a claimed audiobook resumes the player without being downloaded`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("audiobook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = true, audiobookDownloaded = false), isOnline = true)

        assertEquals(PairOpenTarget.Player, target)
    }

    /**
     * And the opposite direction, which is the same rule: with no claim to
     * follow, being online must NOT be treated as a reason to open something.
     * The reader fetches its EPUB on open, so routing there on a first tap
     * downloads a book the user only tapped — exactly what was reported.
     */
    @Test
    fun `online, a pair with nothing downloaded and no claim goes to details`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns null

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = false, audiobookDownloaded = false), isOnline = true)

        assertEquals(PairOpenTarget.Details, target)
    }

    /** A claim is consent, so the ebook may be fetched when it names one. */
    @Test
    fun `online, a claimed ebook opens the reader and lets it fetch the file`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("ebook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = false, audiobookDownloaded = false), isOnline = true)

        assertEquals(PairOpenTarget.Reader, target)
    }

    /** Offline the two axes collapse back together and nothing changes. */
    @Test
    fun `offline, a claimed audiobook that is not downloaded cannot be resumed`() = runTest {
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("audiobook")

        val target = repository().resolvePairOpenTarget(
            pair(ebookDownloaded = false, audiobookDownloaded = false), isOnline = false)

        assertEquals(PairOpenTarget.Details, target)
    }

    /**
     * Cross-platform parity (issue #215): this test and the web's
     * `pairOpenTarget.test.js` drive the *same* golden vectors
     * (server/tests/fixtures/sync_parity/pair_open_target.json, copied onto
     * the test classpath by the copySyncParityFixtures Gradle task), so the
     * two clients cannot disagree about which format a pair opens in.
     *
     * The web disagreed for a long time: it compared the two `user_progress`
     * rows' `updated_at` instead of reading `source`, and since a pair-scoped
     * write stamps both rows in one loop that comparison always tied — a pair
     * last listened to on the phone still opened the web reader.
     *
     * `available` is "what this client can actually open": downloaded here,
     * present on the pair on the web.
     */
    private fun parityCases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/pair_open_target.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonArray }
        ?: error("pair_open_target.json not on the test classpath — copySyncParityFixtures must run")

    @Test
    fun resolvePairOpenTarget_matches_the_shared_golden_vectors() = runTest {
        val cases = parityCases()
        assertTrue("expected at least one golden vector", cases.size > 0)

        for (case in cases) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val why = obj["why"]?.jsonPrimitive?.contentOrNull ?: ""
            val source = obj["source"]?.jsonPrimitive?.contentOrNull
            val available = obj["available"]!!.jsonObject
            val expected = when (val e = obj["expected"]!!.jsonPrimitive.content) {
                "ebook" -> PairOpenTarget.Reader
                "audiobook" -> PairOpenTarget.Player
                "details" -> PairOpenTarget.Details
                else -> error("$name: unknown expected target '$e'")
            }

            val local = obj["local"]?.jsonObject ?: available
            val openEbook = available["ebook"]!!.jsonPrimitive.boolean
            val openAudio = available["audiobook"]!!.jsonPrimitive.boolean
            val localEbook = local["ebook"]!!.jsonPrimitive.boolean
            val localAudio = local["audiobook"]!!.jsonPrimitive.boolean

            // Android has one network flag, not one per format: openable ==
            // downloaded || online. A vector where only *one* format gains
            // openability from being online is therefore inexpressible here —
            // fail loudly rather than quietly testing something else.
            val isOnline = (openEbook && !localEbook) || (openAudio && !localAudio)
            require((localEbook || isOnline) == openEbook && (localAudio || isOnline) == openAudio) {
                "$name: Android cannot express available=$available with local=$local — " +
                    "being online makes both formats openable at once"
            }

            coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns source?.let { bookmark(it) }

            val target = repository().resolvePairOpenTarget(
                pair(ebookDownloaded = localEbook, audiobookDownloaded = localAudio),
                isOnline = isOnline,
            )

            assertEquals("$name: $why", expected, target)
        }
    }

    @Test
    fun `resolvePairOpenTarget by pairId looks up the pair first`() = runTest {
        coEvery { bookPairDao.getPairById(84) } returns pair(ebookDownloaded = true)
        coEvery { bookmarkDao.getBookmark(TEST_SCOPE, 84) } returns bookmark("ebook")

        assertEquals(PairOpenTarget.Reader, repository().resolvePairOpenTarget(84, isOnline = false))
    }

    @Test
    fun `resolvePairOpenTarget by pairId returns Details when the pair is unknown locally`() = runTest {
        coEvery { bookPairDao.getPairById(999) } returns null

        assertEquals(PairOpenTarget.Details, repository().resolvePairOpenTarget(999, isOnline = false))
    }
}
