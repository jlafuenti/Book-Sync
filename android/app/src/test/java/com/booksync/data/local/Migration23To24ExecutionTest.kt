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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Executes [MIGRATION_23_24] - the `sync_point_words` table (issue #836) -
 * against a populated v23 database built from the committed 23.json, with the
 * same JDBC bridge as [Migration22To23ExecutionTest].
 *
 * Word timing is a cache of a server read: a failed or missing fetch only
 * costs the word mark, never a reading position, so the migration is a plain
 * table creation and leaves every existing table alone.
 */
class Migration23To24ExecutionTest {

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

    private fun tableExists(table: String): Boolean =
        conn.createStatement()
            .executeQuery("SELECT name FROM sqlite_master WHERE type = 'table' AND name = '$table'")
            .use { it.next() }

    @Test
    fun `the words table does not exist at v23 and does after the migration`() {
        buildVersion(23)
        assertTrue(!tableExists("sync_point_words"))

        MIGRATION_23_24.migrate(bridge())

        assertTrue(tableExists("sync_point_words"))
    }

    @Test
    fun `the migrated words table has exactly the columns Room expects at v24`() {
        buildVersion(23)

        MIGRATION_23_24.migrate(bridge())

        assertEquals(
            expectedColumns(24, "sync_point_words").sorted(),
            columnsOf("sync_point_words").sorted(),
        )
    }

    @Test
    fun `existing sync points survive untouched`() {
        buildVersion(23)
        exec(
            "INSERT INTO sync_points (bookPairId, epubChapter, epubSentenceIndex, " +
                "epubTextPreview, audioStartMs, audioEndMs, confidence) " +
                "VALUES (7, 1, 2, 'An invented sentence.', 500, 1500, 1.0)"
        )

        MIGRATION_23_24.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM sync_points").use { rs ->
            assertTrue("the row must survive", rs.next())
            assertEquals(7, rs.getInt("bookPairId"))
            assertEquals(500, rs.getInt("audioStartMs"))
        }
    }

    @Test
    fun `a words row round-trips and the composite key replaces on conflict`() {
        buildVersion(23)
        MIGRATION_23_24.migrate(bridge())

        exec("INSERT INTO sync_point_words VALUES (7, 1, 2, '100,450,900')")
        exec("INSERT OR REPLACE INTO sync_point_words VALUES (7, 1, 2, '110,460,910')")

        conn.createStatement().executeQuery("SELECT wordStarts FROM sync_point_words").use { rs ->
            assertTrue(rs.next())
            assertEquals("110,460,910", rs.getString(1))
            assertTrue("the key is (pair, chapter, sentence); one row", !rs.next())
        }
    }
}
