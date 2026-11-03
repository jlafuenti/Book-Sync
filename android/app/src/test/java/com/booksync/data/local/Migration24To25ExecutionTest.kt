package com.booksync.data.local

import androidx.sqlite.db.SupportSQLiteDatabase
import io.mockk.every
import io.mockk.mockk
import java.io.File
import java.sql.Connection
import java.sql.DriverManager
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Executes [MIGRATION_24_25] - several series per book (issue #908) - against a
 * populated v24 database built from the committed 24.json, with the same JDBC
 * bridge as [Migration23To24ExecutionTest].
 *
 * The scalar `series`/`seriesIndex` (and `ebookSeries`/`ebookSeriesIndex` on
 * pairs) become a one-entry JSON array in the new `seriesJson` column, so an
 * offline library keeps its series until the next refresh replaces them with the
 * server's full list. SQLite cannot be relied on to DROP COLUMN on every API
 * level we ship to, so the three tables are rebuilt.
 */
class Migration24To25ExecutionTest {

    private lateinit var conn: Connection

    @Before
    fun setUp() {
        conn = DriverManager.getConnection("jdbc:sqlite::memory:")
    }

    @After
    fun tearDown() {
        conn.close()
    }

    private fun exec(sql: String) = conn.createStatement().use { it.execute(sql) }

    private fun bridge(): SupportSQLiteDatabase =
        mockk<SupportSQLiteDatabase>(relaxed = true).also {
            every { it.execSQL(any()) } answers { exec(firstArg()) }
        }

    private fun schemaDir(): File {
        var dir = File("").absoluteFile
        repeat(4) {
            for (c in listOf(
                File(dir, "app/schemas/com.booksync.data.local.BookSyncDatabase"),
                File(dir, "schemas/com.booksync.data.local.BookSyncDatabase"),
            )) if (c.isDirectory) return c
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("exported schemas not found")
    }

    private fun entities(version: Int) =
        Json.parseToJsonElement(File(schemaDir(), "$version.json").readText())
            .jsonObject["database"]!!.jsonObject["entities"]!!.jsonArray

    private fun buildVersion(version: Int) {
        val placeholder = "$" + "{TABLE_NAME}"
        entities(version).forEach { e ->
            val o = e.jsonObject
            exec(
                o["createSql"]!!.jsonPrimitive.content
                    .replace(placeholder, o["tableName"]!!.jsonPrimitive.content)
            )
        }
    }

    private fun expectedColumns(version: Int, table: String): List<String> =
        entities(version).map { it.jsonObject }
            .first { it["tableName"]!!.jsonPrimitive.content == table }["fields"]!!
            .jsonArray.map { it.jsonObject["columnName"]!!.jsonPrimitive.content }

    private fun columnsOf(table: String): List<String> =
        conn.createStatement().executeQuery("PRAGMA table_info(`$table`)").use { rs ->
            buildList { while (rs.next()) add(rs.getString("name")) }
        }

    private fun insertEbook(id: Int, title: String, series: String?, index: Double?) = exec(
        "INSERT INTO ebooks (id, title, author, filename, fileSize, format, series, seriesIndex, " +
            "uploadedAt, isDownloaded, coverFilename) VALUES ($id, '$title', 'An Author', '$id.epub', " +
            "1024, 'epub', ${series?.let { "'$it'" } ?: "NULL"}, ${index ?: "NULL"}, " +
            "'2026-01-01T00:00:00Z', 1, 'cover$id.jpg')"
    )

    private fun insertAudiobook(id: Int, title: String, series: String?, index: Double?) = exec(
        "INSERT INTO audiobooks (id, title, author, filename, durationSeconds, format, series, " +
            "seriesIndex, uploadedAt, isDownloaded, coverFilename) VALUES ($id, '$title', 'An Author', " +
            "'$id.m4b', 3600, 'm4b', ${series?.let { "'$it'" } ?: "NULL"}, ${index ?: "NULL"}, " +
            "'2026-01-01T00:00:00Z', 0, NULL)"
    )

    private fun insertPair(id: Int, ebookSeries: String?, ebookSeriesIndex: Double?) = exec(
        "INSERT INTO book_pairs (id, ebookId, ebookTitle, ebookAuthor, ebookFilename, ebookFormat, " +
            "audiobookId, audiobookTitle, audiobookAuthor, audiobookFilename, audiobookFormat, " +
            "audiobookDurationSeconds, status, ebookDownloaded, audiobookDownloaded, syncMapDownloaded, " +
            "syncMapVersion, audiobookCoverPath, ebookSeries, ebookSeriesIndex) VALUES ($id, 1, 'Pair $id', " +
            "'An Author', 'p$id.epub', 'epub', 2, 'Pair $id', 'A Narrator', 'p$id.m4b', 'm4b', 7200, " +
            "'synced', 1, 1, 1, 3, 'pair$id.jpg', ${ebookSeries?.let { "'$it'" } ?: "NULL"}, " +
            "${ebookSeriesIndex ?: "NULL"})"
    )

    private fun seriesJsonOf(table: String, id: Int): String? =
        conn.createStatement().executeQuery("SELECT seriesJson FROM $table WHERE id = $id").use { rs ->
            assertTrue("row $id must survive in $table", rs.next())
            rs.getString(1)
        }

    @Test
    fun `the old scalar series columns are gone after the migration`() {
        buildVersion(24)

        MIGRATION_24_25.migrate(bridge())

        for (table in listOf("ebooks", "audiobooks")) {
            assertFalse(columnsOf(table).contains("series"))
            assertFalse(columnsOf(table).contains("seriesIndex"))
            assertTrue(columnsOf(table).contains("seriesJson"))
        }
        assertFalse(columnsOf("book_pairs").contains("ebookSeries"))
        assertFalse(columnsOf("book_pairs").contains("ebookSeriesIndex"))
        assertTrue(columnsOf("book_pairs").contains("seriesJson"))
    }

    @Test
    fun `the migrated tables have exactly the columns Room expects at v25`() {
        buildVersion(24)

        MIGRATION_24_25.migrate(bridge())

        for (table in listOf("ebooks", "audiobooks", "book_pairs")) {
            assertEquals(
                "columns of $table",
                expectedColumns(25, table).sorted(),
                columnsOf(table).sorted(),
            )
        }
    }

    @Test
    fun `a series and its number become one JSON entry`() {
        buildVersion(24)
        insertEbook(1, "Axis Three", "Axis Saga", 3.0)
        insertAudiobook(2, "Axis Three", "Axis Saga", 3.0)
        insertPair(5, "Axis Saga", 3.0)

        MIGRATION_24_25.migrate(bridge())

        val expected = listOf(SeriesEntry("Axis Saga", 3.0))
        assertEquals(expected, decodeSeries(seriesJsonOf("ebooks", 1)))
        assertEquals(expected, decodeSeries(seriesJsonOf("audiobooks", 2)))
        assertEquals(expected, decodeSeries(seriesJsonOf("book_pairs", 5)))
    }

    @Test
    fun `a fractional number survives and a missing number stays missing`() {
        buildVersion(24)
        insertEbook(1, "Axis Novella", "Axis Saga", 2.5)
        insertEbook(2, "Axis Unnumbered", "Axis Saga", null)

        MIGRATION_24_25.migrate(bridge())

        assertEquals(listOf(SeriesEntry("Axis Saga", 2.5)), decodeSeries(seriesJsonOf("ebooks", 1)))
        assertEquals(listOf(SeriesEntry("Axis Saga", null)), decodeSeries(seriesJsonOf("ebooks", 2)))
    }

    @Test
    fun `a NULL or empty series stays NULL`() {
        buildVersion(24)
        insertEbook(1, "Standalone", null, null)
        insertEbook(2, "Cleared", "", null)
        insertAudiobook(3, "Standalone", null, 4.0)
        insertPair(5, null, null)

        MIGRATION_24_25.migrate(bridge())

        assertNull(seriesJsonOf("ebooks", 1))
        assertNull(seriesJsonOf("ebooks", 2))
        assertNull(seriesJsonOf("audiobooks", 3))
        assertNull(seriesJsonOf("book_pairs", 5))
    }

    @Test
    fun `quotes and backslashes in a name are escaped into valid JSON`() {
        buildVersion(24)
        insertEbook(1, "Odd", "The \"Axis\" Saga", 1.0)

        MIGRATION_24_25.migrate(bridge())

        assertEquals(listOf(SeriesEntry("The \"Axis\" Saga", 1.0)), decodeSeries(seriesJsonOf("ebooks", 1)))
    }

    @Test
    fun `every other column of every row survives`() {
        buildVersion(24)
        insertEbook(1, "Axis Three", "Axis Saga", 3.0)
        insertAudiobook(2, "Axis Three", "Axis Saga", 3.0)
        insertPair(5, "Axis Saga", 3.0)

        MIGRATION_24_25.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM ebooks").use { rs ->
            assertTrue(rs.next())
            assertEquals("Axis Three", rs.getString("title"))
            assertEquals(1024L, rs.getLong("fileSize"))
            assertEquals(1, rs.getInt("isDownloaded"))
            assertEquals("cover1.jpg", rs.getString("coverFilename"))
        }
        conn.createStatement().executeQuery("SELECT * FROM audiobooks").use { rs ->
            assertTrue(rs.next())
            assertEquals(3600, rs.getInt("durationSeconds"))
            assertEquals("m4b", rs.getString("format"))
        }
        conn.createStatement().executeQuery("SELECT * FROM book_pairs").use { rs ->
            assertTrue(rs.next())
            assertEquals("synced", rs.getString("status"))
            assertEquals(3, rs.getInt("syncMapVersion"))
            assertEquals("pair5.jpg", rs.getString("audiobookCoverPath"))
            assertEquals("A Narrator", rs.getString("audiobookAuthor"))
            assertEquals(7200, rs.getInt("audiobookDurationSeconds"))
        }
    }
}
