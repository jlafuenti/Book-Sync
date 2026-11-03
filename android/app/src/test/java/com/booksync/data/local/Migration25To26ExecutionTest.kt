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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Executes [MIGRATION_25_26] - the `next_up_dismissals` table for "Remove from
 * Next up" (issue #923) - against a populated v25 database built from the
 * committed 25.json, with the same JDBC bridge as [Migration24To25ExecutionTest].
 *
 * Purely additive: nothing existing may be touched, and the new table must be
 * exactly what Room validates against 26.json.
 */
class Migration25To26ExecutionTest {

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

    private fun tables(): Set<String> =
        conn.createStatement().executeQuery("SELECT name FROM sqlite_master WHERE type = 'table'").use { rs ->
            buildSet { while (rs.next()) add(rs.getString(1)) }
        }

    @Test
    fun `the table does not exist at v25 and does after the migration`() {
        buildVersion(25)
        assertFalse(tables().contains("next_up_dismissals"))

        MIGRATION_25_26.migrate(bridge())

        assertTrue(tables().contains("next_up_dismissals"))
    }

    @Test
    fun `the new table has exactly the columns Room expects at v26`() {
        buildVersion(25)

        MIGRATION_25_26.migrate(bridge())

        assertEquals(
            expectedColumns(26, "next_up_dismissals").sorted(),
            columnsOf("next_up_dismissals").sorted(),
        )
    }

    @Test
    fun `the series key is the primary key`() {
        buildVersion(25)
        MIGRATION_25_26.migrate(bridge())

        exec("INSERT INTO next_up_dismissals (seriesKey, series, dismissedAtMs) VALUES ('axis saga', 'Axis Saga', 1)")
        val duplicate = runCatching {
            exec("INSERT INTO next_up_dismissals (seriesKey, series, dismissedAtMs) VALUES ('axis saga', 'Axis Saga', 2)")
        }
        assertTrue("a second row for one series key must be refused", duplicate.isFailure)
    }

    @Test
    fun `existing tables and rows are untouched`() {
        buildVersion(25)
        exec(
            "INSERT INTO acknowledged_items (scopeKey, itemId, itemType) VALUES ('https://a.example.com|1', 7, 'pair')"
        )
        val before = tables()

        MIGRATION_25_26.migrate(bridge())

        assertEquals(before + "next_up_dismissals", tables())
        conn.createStatement().executeQuery("SELECT COUNT(*) FROM acknowledged_items").use {
            it.next()
            assertEquals(1, it.getInt(1))
        }
    }

    @Test
    fun `running it twice is harmless`() {
        buildVersion(25)
        MIGRATION_25_26.migrate(bridge())
        MIGRATION_25_26.migrate(bridge())
        assertTrue(tables().contains("next_up_dismissals"))
    }
}
