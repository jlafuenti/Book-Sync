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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Executes [MIGRATION_20_21] — the ebook cover column — against a populated v20
 * database built from the committed 20.json, the same JDBC bridge as
 * [Migration19To20ExecutionTest].
 */
class Migration20To21ExecutionTest {

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

    @Test
    fun `an existing ebook survives and gains an empty cover column`() {
        buildVersion(20)
        exec(
            "INSERT INTO ebooks (id, title, author, filename, fileSize, format, series, seriesIndex, " +
                "uploadedAt, isDownloaded) VALUES " +
                "(5, 'Title', 'Author', 'e.epub', 1024, 'epub', 'Series', 2.0, '2026-01-01T00:00:00', 1)"
        )

        MIGRATION_20_21.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM ebooks").use { rs ->
            assertTrue("the row must survive", rs.next())
            assertEquals(5, rs.getInt("id"))
            assertEquals("Title", rs.getString("title"))
            // A download the user already has must not be forgotten.
            assertEquals(1, rs.getInt("isDownloaded"))
            // Filled in by the next library refresh, not by the migration.
            assertNull(rs.getString("coverFilename"))
        }
    }

    @Test
    fun `the migrated ebooks table has exactly the columns Room expects at v21`() {
        buildVersion(20)

        MIGRATION_20_21.migrate(bridge())

        assertEquals(expectedColumns(21, "ebooks").sorted(), columnsOf("ebooks").sorted())
    }
}
