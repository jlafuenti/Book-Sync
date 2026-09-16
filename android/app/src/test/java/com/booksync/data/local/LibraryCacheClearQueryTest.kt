package com.booksync.data.local

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
 * What the library-cache clear of issue #575 actually deletes, run as SQL.
 *
 * The line that matters is the one it must **not** cross. `bookmarks`,
 * `user_progress`, `bookmark_log`, `pending_sync` and `acknowledged_items` hold
 * `syncedToServer = 0` rows the server has never seen — positions that exist
 * nowhere else — and are already partitioned by `(server, user)` since issue
 * #314, so the wrong account cannot read them anyway. Deleting them to fix a
 * library leak is the mistake #314 was filed to avoid.
 *
 * There is no `androidTest` source set here, so Room cannot run: this executes
 * the real statements, read out of `LibraryCacheDao.kt`, against an in-memory
 * SQLite built from the committed schema export — the JDBC bridge
 * [RecentlyPlayedQueryTest] and [Migration20To21ExecutionTest] use.
 */
class LibraryCacheClearQueryTest {

    private val libraryTables = listOf("book_pairs", "ebooks", "audiobooks", "sync_points")
    private val personalTables = listOf(
        "bookmarks", "user_progress", "bookmark_log", "pending_sync", "acknowledged_items",
    )

    private lateinit var conn: Connection

    @Before
    fun setUp() {
        conn = DriverManager.getConnection("jdbc:sqlite::memory:")
        for (table in libraryTables + personalTables) exec(createSqlFor(table))
    }

    @After
    fun tearDown() = conn.close()

    // --- the schema and the statements, both read from the repository -------

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
        repoFile("src/main/java/com/booksync/data/local/dao/LibraryCacheDao.kt").readText()
    }

    /**
     * Every `@Query` in `LibraryCacheDao` that is a DELETE, read from the source
     * rather than restated here — a copy in the test would keep passing after
     * the shipped statement changed.
     */
    private fun clearStatements(): List<String> =
        Regex("@Query\\(\"(DELETE[^\"]*)\"\\)").findAll(daoSource)
            .map { it.groupValues[1] }.toList()

    private fun exec(sql: String) = conn.createStatement().use { it.execute(sql) }

    private fun count(table: String): Int =
        conn.createStatement().executeQuery("SELECT COUNT(*) FROM $table").use {
            it.next(); it.getInt(1)
        }

    // --- fixtures ------------------------------------------------------------

    private fun populate() {
        exec(
            """
            INSERT INTO book_pairs (id, ebookId, ebookTitle, ebookFilename, ebookFormat,
                audiobookId, audiobookTitle, audiobookFilename, audiobookFormat, status,
                ebookDownloaded, audiobookDownloaded, syncMapDownloaded)
            VALUES (1, 1, 'Ebook', 'e1.epub', 'epub', 1, 'Audio', 'a1.m4b', 'm4b', 'synced', 1, 1, 1)
            """.trimIndent()
        )
        exec(
            "INSERT INTO ebooks (id, title, filename, format, uploadedAt, isDownloaded) " +
                "VALUES (1, 'Ebook', 'e1.epub', 'epub', '2026-01-01T00:00:00', 1)"
        )
        exec(
            "INSERT INTO audiobooks (id, title, filename, format, uploadedAt, isDownloaded) " +
                "VALUES (1, 'Audio', 'a1.m4b', 'm4b', '2026-01-01T00:00:00', 1)"
        )
        exec(
            "INSERT INTO sync_points (bookPairId, epubChapter, epubSentenceIndex, " +
                "epubTextPreview, audioStartMs, audioEndMs, confidence) " +
                "VALUES (1, 0, 0, 'text', 0, 1000, 1.0)"
        )
        exec(
            "INSERT INTO bookmarks (scopeKey, bookPairId, source, epubChapter, epubSentenceIndex, " +
                "audioPositionMs, updatedAt, syncedToServer) " +
                "VALUES ('https://a.example.com|1', 1, 'audiobook', 0, 0, 1000, '1788220800000', 0)"
        )
        exec(
            "INSERT INTO user_progress (scopeKey, mediaType, mediaId, isCompleted, updatedAt, " +
                "syncedToServer) VALUES ('https://a.example.com|1', 'audiobook', 1, 0, 1788220800000, 0)"
        )
        exec(
            "INSERT INTO bookmark_log (scopeKey, bookPairId, source, prevEpubChapter, " +
                "prevEpubSentenceIndex, prevAudioPositionMs, newEpubChapter, newEpubSentenceIndex, " +
                "newAudioPositionMs, changedAt) " +
                "VALUES ('https://a.example.com|1', 1, 'audiobook', 0, 0, 0, 0, 1, 1000, '2026-01-01T00:00:00Z')"
        )
        exec(
            "INSERT INTO pending_sync (scopeKey, bookPairId, source, epubChapter, " +
                "epubSentenceIndex, audioPositionMs, appendToLog, createdAt) " +
                "VALUES ('https://a.example.com|1', 1, 'audiobook', 0, 0, 1000, 0, 1788220800000)"
        )
        exec(
            "INSERT INTO acknowledged_items (scopeKey, itemId, itemType) " +
                "VALUES ('https://a.example.com|1', 1, 'pair')"
        )
    }

    private fun runClear() = clearStatements().forEach { exec(it) }

    // --- tests ---------------------------------------------------------------

    @Test
    fun `the clear empties every library table`() {
        populate()

        runClear()

        for (table in libraryTables) {
            assertEquals("$table still holds the other server's rows", 0, count(table))
        }
    }

    @Test
    fun `the clear never touches a per-account table`() {
        populate()

        runClear()

        for (table in personalTables) {
            assertEquals(
                "$table holds positions the server has never seen and is already " +
                    "scoped by (server, user) since issue #314 — deleting it to fix " +
                    "a library leak is the mistake #314 was filed to avoid",
                1, count(table),
            )
        }
    }

    @Test
    fun `the statements actually came from the DAO`() {
        // A silently-empty read would make both tests above pass for the wrong
        // reason — the same failure mode as the scan they replace.
        val statements = clearStatements()
        assertEquals(
            "expected one DELETE per library table in LibraryCacheDao",
            libraryTables.size, statements.size,
        )
        for (table in libraryTables) {
            assertTrue(
                "no DELETE for $table in LibraryCacheDao",
                statements.any { it.contains(Regex("\\bFROM\\s+$table\\b")) },
            )
        }
    }
}
