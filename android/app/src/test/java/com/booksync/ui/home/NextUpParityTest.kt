package com.booksync.ui.home

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.TEST_SCOPE
import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #716: "Next up", the next book in each series you are reading.
 *
 * Cross-platform parity: this suite and the web's `lib/nextUp.test.js` load the
 * *same* golden vectors (`server/tests/fixtures/sync_parity/next_up_cases.json`,
 * copied onto the test classpath by the build), so the app's row and the web's
 * cannot disagree about which book comes next.
 */
class NextUpParityTest {

    private fun cases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/next_up_cases.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonArray }
        ?: error("next_up_cases.json is not on the test classpath")

    @Test
    fun `computeNextUp matches the shared golden vectors`() {
        val all = cases()
        assertTrue("expected the shared vectors", all.isNotEmpty())
        all.forEach { element ->
            val case = element.jsonObject
            val name = case["name"]!!.jsonPrimitive.content
            val books = case["books"]!!.jsonArray.map { b ->
                val o = b.jsonObject
                NextUpBook(
                    key = o["key"]!!.jsonPrimitive.content,
                    series = o["series"]?.takeIf { it != JsonNull }?.jsonPrimitive?.contentOrNull,
                    index = o["index"]?.takeIf { it != JsonNull }?.jsonPrimitive?.doubleOrNull,
                )
            }
            val activity = case["activity"]!!.jsonArray.map { a ->
                val o = a.jsonObject
                NextUpActivity(
                    key = o["key"]!!.jsonPrimitive.content,
                    completed = o["completed"]!!.jsonPrimitive.boolean,
                    atMs = Instant.parse(o["at"]!!.jsonPrimitive.content).toEpochMilli(),
                )
            }
            val now = Instant.parse(case["now"]!!.jsonPrimitive.content).toEpochMilli()
            val expected = case["expected"]!!.jsonArray.map { e ->
                e.jsonObject["series"]!!.jsonPrimitive.content to e.jsonObject["key"]!!.jsonPrimitive.content
            }

            val actual = computeNextUp(books, activity, now).map { it.series to it.book.key }

            assertEquals(name, expected, actual)
        }
    }

    @Test
    fun `the window is 90 days, the owner-chosen value`() {
        assertEquals(90L, NEXT_UP_WINDOW_DAYS)
    }

    // ---- the Room library, reduced to the rule's inputs -----------------------

    private fun ebook(id: Int, series: String?, index: Float?) = EBookEntity(
        id = id, title = "Ebook $id", author = "An Author", filename = "e$id.epub", fileSize = 1L,
        format = "epub", series = series, seriesIndex = index,
        uploadedAt = "2026-01-01T00:00:00Z", isDownloaded = false,
    )

    private fun audiobook(id: Int, series: String?, index: Float?) = AudioBookEntity(
        id = id, title = "Audiobook $id", author = "An Author", filename = "a$id.m4b", durationSeconds = 60,
        format = "m4b", series = series, seriesIndex = index,
        uploadedAt = "2026-01-01T00:00:00Z", isDownloaded = false,
    )

    private fun pair(id: Int, ebookId: Int, audiobookId: Int, series: String?, index: Float?) = BookPairEntity(
        id = id, ebookId = ebookId, ebookTitle = "Ebook $ebookId", ebookAuthor = "An Author",
        ebookFilename = "e$ebookId.epub", ebookFormat = "epub",
        audiobookId = audiobookId, audiobookTitle = "Audiobook $audiobookId", audiobookAuthor = "An Author",
        audiobookFilename = "a$audiobookId.m4b", audiobookFormat = "m4b", audiobookDurationSeconds = 60,
        status = "synced", ebookSeries = series, ebookSeriesIndex = index,
    )

    private fun progress(
        mediaType: String, mediaId: Int, completed: Boolean = false,
        percent: Float? = null, audioMs: Int? = null, capturedAt: String? = null, pairId: Int? = null,
    ) = UserProgressEntity(
        scopeKey = TEST_SCOPE, mediaType = mediaType, mediaId = mediaId, bookPairId = pairId,
        epubCfi = null, epubChapter = null, epubProgressPercent = percent, audioPositionMs = audioMs,
        isCompleted = completed, updatedAt = 1_000L, deviceId = null, capturedAt = capturedAt,
    )

    @Test
    fun `a pair counts once and carries its ids for the details page`() {
        val (books, _) = libraryToNextUpInput(
            pairs = listOf(pair(5, ebookId = 1, audiobookId = 2, series = "Axis", index = 1f)),
            ebooks = listOf(ebook(1, "Axis", 1f), ebook(3, "Axis", 2f)),
            audiobooks = listOf(audiobook(2, "Axis", 1f)),
            progress = emptyList(),
            bookmarks = emptyList(),
        )

        assertEquals(listOf("pair_5", "ebook_3"), books.map { it.key })
        assertEquals(5, books[0].pairId)
        assertEquals(1.0, books[0].index!!, 0.0)
    }

    @Test
    fun `a pair's series falls back to its audiobook`() {
        val (books, _) = libraryToNextUpInput(
            pairs = listOf(pair(5, ebookId = 1, audiobookId = 2, series = null, index = null)),
            ebooks = listOf(ebook(1, null, null)),
            audiobooks = listOf(audiobook(2, "Axis", 4f)),
            progress = emptyList(),
            bookmarks = emptyList(),
        )

        assertEquals("Axis", books.single().series)
        assertEquals(4.0, books.single().index!!, 0.0)
    }

    @Test
    fun `progress on either half and the pair's bookmark are filed under the pair`() {
        val (_, activity) = libraryToNextUpInput(
            pairs = listOf(pair(5, ebookId = 1, audiobookId = 2, series = "Axis", index = 1f)),
            ebooks = listOf(ebook(1, "Axis", 1f)),
            audiobooks = listOf(audiobook(2, "Axis", 1f)),
            progress = listOf(progress("audiobook", 2, audioMs = 60_000, capturedAt = "2026-09-20T00:00:00Z")),
            bookmarks = listOf(
                BookmarkEntity(
                    scopeKey = TEST_SCOPE, bookPairId = 5, source = "audiobook", epubChapter = null,
                    epubSentenceIndex = null, audioPositionMs = 90_000, updatedAt = "2026-09-21T00:00:00Z",
                ),
            ),
        )

        assertEquals(setOf("pair_5"), activity.map { it.key }.toSet())
        assertEquals(2, activity.size)
    }

    @Test
    fun `a book opened at 0 percent and never finished is not being read`() {
        val (_, activity) = libraryToNextUpInput(
            pairs = emptyList(),
            ebooks = listOf(ebook(1, "Axis", 1f), ebook(2, "Axis", 2f)),
            audiobooks = emptyList(),
            progress = listOf(
                progress("ebook", 1, percent = 0f, capturedAt = "2026-09-20T00:00:00Z"),
                progress("ebook", 2, completed = true, capturedAt = "2026-09-20T00:00:00Z"),
            ),
            bookmarks = emptyList(),
        )

        assertEquals(listOf("ebook_2"), activity.map { it.key })
    }
}
