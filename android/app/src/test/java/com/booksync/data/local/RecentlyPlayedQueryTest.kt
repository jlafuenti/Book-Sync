package com.booksync.data.local

import com.booksync.data.remote.PositionResponse
import com.booksync.data.repository.toBookmarkEntity
import java.io.File
import java.sql.Connection
import java.sql.DriverManager
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * What the "recently played / recently read" queries are allowed to filter on
 * (issue #569).
 *
 * Android Auto's **Continue Listening** tab and Home's **Continue Reading** row
 * are both built from these three queries. Two of them still required the audio
 * to be downloaded — a condition that stopped describing playback when issue
 * #171 made an undownloaded book stream. `AudioPlayerService.buildLibraryItems`
 * was updated for that and carries the reasoning in a comment; these were not.
 * The result on a device with nothing downloaded: a book played in the car for
 * four minutes was simply absent from Continue Listening at the next connect,
 * and from the "recent first" half of the voice-search index with it.
 *
 * There is no `androidTest` source set in this project, so Room itself cannot
 * run here. This executes the **real SQL**, lifted verbatim out of `Daos.kt`,
 * against an in-memory SQLite built from the committed schema export — the same
 * JDBC bridge [Migration20To21ExecutionTest] uses. What that proves is the
 * query's own behaviour; what it cannot prove is that Room still binds and maps
 * it (a compile-time concern Room checks itself) or that the car shows the row.
 */
class RecentlyPlayedQueryTest {

    private lateinit var conn: Connection

    private val scope = "acct"
    private val otherScope = "other-acct"

    @Before
    fun setUp() {
        conn = DriverManager.getConnection("jdbc:sqlite::memory:")
        for (table in listOf("book_pairs", "ebooks", "audiobooks", "bookmarks", "user_progress")) {
            exec(createSqlFor(table))
        }
    }

    @After
    fun tearDown() = conn.close()

    // --- The schema and the queries, both read from the repository ----------

    private fun repoFile(relativePath: String): File {
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(File(dir, "app/$relativePath"), File(dir, relativePath))) {
                if (candidate.exists()) return candidate
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    /** The latest exported Room schema — the shape the app actually ships. */
    private fun createSqlFor(table: String): String {
        val dir = repoFile("schemas/com.booksync.data.local.BookSyncDatabase")
        val latest = dir.listFiles { f: File -> f.name.endsWith(".json") }!!
            .maxOf { it.name.removeSuffix(".json").toInt() }
        val entities = Json.parseToJsonElement(File(dir, "$latest.json").readText())
            .jsonObject["database"]!!.jsonObject["entities"]!!.jsonArray
        val entity = entities.map { it.jsonObject }
            .firstOrNull { it["tableName"]!!.jsonPrimitive.content == table }
            ?: throw AssertionError("no entity for $table in schema $latest")
        return entity["createSql"]!!.jsonPrimitive.content.replace("\${TABLE_NAME}", table)
    }

    private val daoSource by lazy {
        repoFile("src/main/java/com/booksync/data/local/dao/Daos.kt").readText()
    }

    /**
     * The `@Query` text annotating [function], with `:scope` bound.
     *
     * Reading the source rather than restating the SQL is the point: a copy in
     * the test would keep passing after the shipped query changed.
     */
    private fun sqlFor(function: String): String {
        val at = daoSource.indexOf("fun $function(")
        assertTrue("No function named $function in Daos.kt", at > 0)
        val annotation = daoSource.lastIndexOf("@Query(", at)
        assertTrue("$function is not annotated with @Query", annotation in 0 until at)
        val body = daoSource.substring(annotation, at)
        val sql = Regex("\"\"\"(.*?)\"\"\"", RegexOption.DOT_MATCHES_ALL).find(body)?.groupValues?.get(1)
            ?: Regex("@Query\\(\"(.*?)\"\\)", RegexOption.DOT_MATCHES_ALL).find(body)?.groupValues?.get(1)
            ?: throw AssertionError("Could not read the SQL for $function")
        return sql.replace(":scope", "'$scope'")
    }

    private fun exec(sql: String) = conn.createStatement().use { it.execute(sql) }

    private fun idsFrom(function: String): List<Int> =
        conn.createStatement().use { st ->
            st.executeQuery(sqlFor(function)).use { rs ->
                buildList { while (rs.next()) add(rs.getInt("id")) }
            }
        }

    // --- Fixtures -----------------------------------------------------------

    private fun insertPair(id: Int, audiobookDownloaded: Boolean) = exec(
        """
        INSERT INTO book_pairs (id, ebookId, ebookTitle, ebookFilename, ebookFormat,
            audiobookId, audiobookTitle, audiobookFilename, audiobookFormat, status,
            ebookDownloaded, audiobookDownloaded, syncMapDownloaded)
        VALUES ($id, $id, 'Ebook $id', 'e$id.epub', 'epub',
            $id, 'Audio $id', 'a$id.m4b', 'm4b', 'synced',
            0, ${if (audiobookDownloaded) 1 else 0}, 0)
        """.trimIndent()
    )

    private fun insertBookmark(pairId: Int, positionMs: Long, updatedAt: String, scopeKey: String = scope) = exec(
        """
        INSERT INTO bookmarks (scopeKey, bookPairId, source, audioPositionMs, updatedAt, syncedToServer)
        VALUES ('$scopeKey', $pairId, 'audiobook', $positionMs, '$updatedAt', 0)
        """.trimIndent()
    )

    private fun insertAudiobook(id: Int, isDownloaded: Boolean) = exec(
        """
        INSERT INTO audiobooks (id, title, filename, format, uploadedAt, isDownloaded)
        VALUES ($id, 'Audiobook $id', 'a$id.m4b', 'm4b', '2026-01-01T00:00:00',
            ${if (isDownloaded) 1 else 0})
        """.trimIndent()
    )

    private fun insertEbook(id: Int, isDownloaded: Boolean) = exec(
        """
        INSERT INTO ebooks (id, title, filename, format, uploadedAt, isDownloaded)
        VALUES ($id, 'Ebook $id', 'e$id.epub', 'epub', '2026-01-01T00:00:00',
            ${if (isDownloaded) 1 else 0})
        """.trimIndent()
    )

    private fun insertProgress(
        mediaType: String,
        mediaId: Int,
        audioPositionMs: Long? = null,
        epubProgressPercent: Double? = null,
        isCompleted: Boolean = false,
        updatedAt: Long = 1_788_220_800_000L,
        scopeKey: String = scope,
    ) = exec(
        """
        INSERT INTO user_progress (scopeKey, mediaType, mediaId, audioPositionMs,
            epubProgressPercent, isCompleted, updatedAt, syncedToServer)
        VALUES ('$scopeKey', '$mediaType', $mediaId, ${audioPositionMs ?: "NULL"},
            ${epubProgressPercent ?: "NULL"}, ${if (isCompleted) 1 else 0}, $updatedAt, 0)
        """.trimIndent()
    )

    // --- Continue Listening: a streamed book counts as played ---------------

    /** The issue's own reproduction, for a paired book. */
    @Test
    fun `a pair whose audio is not downloaded is recently played`() {
        insertPair(1, audiobookDownloaded = false)
        insertBookmark(1, positionMs = 240_000, updatedAt = "1788220800000")

        assertEquals(
            "A streamed pair with four minutes of audio progress must reach " +
                "Continue Listening (issue #569).",
            listOf(1),
            idsFrom("getRecentlyPlayedPairs"),
        )
    }

    /** The exact case walked on the Desktop Head Unit: a standalone, streamed book. */
    @Test
    fun `a standalone audiobook that is not downloaded is recently played`() {
        insertAudiobook(7, isDownloaded = false)
        insertProgress("audiobook", 7, audioPositionMs = 240_000)

        assertEquals(
            "A streamed standalone audiobook with audio progress must reach " +
                "Continue Listening (issue #569).",
            listOf(7),
            idsFrom("getRecentlyPlayedStandaloneAudiobooks"),
        )
    }

    /**
     * The shapes `bookmarks.updatedAt` can hold, end to end (issue #617).
     *
     * `ORDER BY b.updatedAt DESC` is a TEXT sort, so an ISO datetime
     * (`'2026-...'`) outranks every epoch-millis string (`'17...'`) whatever
     * the actual times are. This drives the values through the real ingest
     * mapper rather than hard-coding them, so the sort and the normalisation
     * that makes it valid are pinned together: relax one and this fails.
     */
    @Test
    fun `a server-pulled position sorts by its real age, not its spelling`() {
        fun pulledUpdatedAt(iso: String): String = PositionResponse(
            scope = "pair",
            book_pair_id = 1,
            source = "audiobook",
            anchor_revision = 1L,
            updated_at = iso,
        ).toBookmarkEntity(scope, 1, null).updatedAt

        insertPair(1, audiobookDownloaded = false)
        insertPair(2, audiobookDownloaded = false)
        // Pair 1 came from the server and is genuinely OLDER than pair 2,
        // which was written locally. Unnormalised, pair 1 would win the sort.
        insertBookmark(1, positionMs = 1_000, updatedAt = pulledUpdatedAt("2026-08-17T00:18:40.003994"))
        insertBookmark(2, positionMs = 1_000, updatedAt = "1788220800000")

        assertEquals(listOf(2, 1), idsFrom("getRecentlyPlayedPairs"))
    }

    /**
     * Downloaded and streamed books are the *same* list, most recent first.
     * Splitting them, or ordering downloads ahead, would be a different bug in
     * the tab whose whole job is recency.
     */
    @Test
    fun `downloaded and streamed pairs interleave by recency`() {
        insertPair(1, audiobookDownloaded = true)
        insertPair(2, audiobookDownloaded = false)
        insertBookmark(1, positionMs = 1_000, updatedAt = "1788220800000")
        insertBookmark(2, positionMs = 1_000, updatedAt = "1788566400000")

        assertEquals(listOf(2, 1), idsFrom("getRecentlyPlayedPairs"))
    }

    @Test
    fun `downloaded and streamed standalone audiobooks interleave by recency`() {
        insertAudiobook(1, isDownloaded = true)
        insertAudiobook(2, isDownloaded = false)
        insertProgress("audiobook", 1, audioPositionMs = 1_000, updatedAt = 1_788_220_800_000L)
        insertProgress("audiobook", 2, audioPositionMs = 1_000, updatedAt = 1_788_566_400_000L)

        assertEquals(listOf(2, 1), idsFrom("getRecentlyPlayedStandaloneAudiobooks"))
    }

    // --- What the two queries must still exclude ---------------------------

    @Test
    fun `a book that was never played is not recently played`() {
        insertPair(1, audiobookDownloaded = false)
        insertBookmark(1, positionMs = 0, updatedAt = "1788220800000")
        insertAudiobook(7, isDownloaded = false)
        insertProgress("audiobook", 7, audioPositionMs = null)

        assertEquals(emptyList<Int>(), idsFrom("getRecentlyPlayedPairs"))
        assertEquals(emptyList<Int>(), idsFrom("getRecentlyPlayedStandaloneAudiobooks"))
    }

    /**
     * Scoping is the other account's reading data (issue #314) and is not this
     * issue's to relax. Dropping a predicate from a WHERE clause is exactly the
     * edit that could take the neighbouring one with it.
     */
    @Test
    fun `another account's progress stays out of both queries`() {
        insertPair(1, audiobookDownloaded = false)
        insertBookmark(1, positionMs = 240_000, updatedAt = "1788220800000", scopeKey = otherScope)
        insertAudiobook(7, isDownloaded = false)
        insertProgress("audiobook", 7, audioPositionMs = 240_000, scopeKey = otherScope)

        assertEquals(emptyList<Int>(), idsFrom("getRecentlyPlayedPairs"))
        assertEquals(emptyList<Int>(), idsFrom("getRecentlyPlayedStandaloneAudiobooks"))
    }

    // --- The ebook query keeps its download filter, on purpose -------------

    /**
     * Continue **Reading** is not Continue Listening, because an unpaired ebook
     * has nowhere to stream to. Home routes an ebook row to
     * `StandaloneReaderScreen`, which — unlike `ReaderScreen`, which fetches a
     * pair's EPUB on open (issue #171) — has no download shell at all and
     * documents that its callers guarantee the file is present. Listing an
     * undownloaded ebook here would open an empty reader, which is worse than
     * the row being missing. Changing that means giving the standalone reader a
     * download shell first, in a change that can test it.
     */
    @Test
    fun `an ebook that is not downloaded is deliberately not recently read`() {
        insertEbook(3, isDownloaded = false)
        insertProgress("ebook", 3, epubProgressPercent = 42.0)

        assertEquals(
            "getRecentlyReadEbooks must keep its isDownloaded filter until " +
                "StandaloneReaderScreen can open a book that is not on the device.",
            emptyList<Int>(),
            idsFrom("getRecentlyReadEbooks"),
        )
    }

    @Test
    fun `a downloaded ebook with progress is recently read`() {
        insertEbook(3, isDownloaded = true)
        insertProgress("ebook", 3, epubProgressPercent = 42.0)

        assertEquals(listOf(3), idsFrom("getRecentlyReadEbooks"))
    }

    // --- Source guard ------------------------------------------------------

    /**
     * The executing tests above would also pass if someone re-added the filter
     * in a form SQLite happens not to apply to the rows they chose. This says
     * the words out loud, in the style of `AutoWiringTest` and `SyncWiringTest`,
     * so the failure message names the issue rather than a row count.
     */
    @Test
    fun `neither Continue Listening query filters on download state`() {
        for (function in listOf("getRecentlyPlayedPairs", "getRecentlyPlayedStandaloneAudiobooks")) {
            val sql = sqlFor(function)
            val offenders = listOf("audiobookDownloaded", "isDownloaded", "ebookDownloaded")
                .filter { it in sql }
            assertTrue(
                "$function must not filter on download state: found $offenders. " +
                    "Since issue #171 an undownloaded book streams, so a download " +
                    "predicate here hides books that are being listened to — the " +
                    "bug in issue #569. The Downloaded tab has its own queries " +
                    "(getDownloadedPairs / getDownloadedAudioBooks) and keeps them.",
                offenders.isEmpty(),
            )
        }
    }

    /** A guard that found no SQL at all would pass every assertion above. */
    @Test
    fun `the guard actually reads the shipped queries`() {
        for (function in listOf(
            "getRecentlyPlayedPairs",
            "getRecentlyPlayedStandaloneAudiobooks",
            "getRecentlyReadEbooks",
        )) {
            val sql = sqlFor(function).lowercase()
            assertTrue("$function: no SELECT was read from Daos.kt", "select" in sql)
            assertTrue("$function: the scope binding was not substituted", ":scope" !in sql)
        }
        // And the Downloaded tab's own queries still filter, which is what makes
        // the assertion above a statement about Continue Listening rather than
        // about download filters in general.
        assertTrue(
            "getDownloadedPairs must keep filtering on download state.",
            "audiobookDownloaded" in sqlFor("getDownloadedPairs"),
        )
        assertTrue(
            "getDownloadedAudioBooks must keep filtering on download state.",
            "isDownloaded" in sqlFor("getDownloadedAudioBooks"),
        )
    }
}
