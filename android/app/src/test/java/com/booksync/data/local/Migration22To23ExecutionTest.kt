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
 * Executes [MIGRATION_22_23] — `bookmarks.epubTextPreview` (issue #643) —
 * against a populated v22 database built from the committed 22.json, the same
 * JDBC bridge as [Migration20To21ExecutionTest].
 *
 * The text at the position is the strongest rung of both restore ladders: it
 * survives re-parsing and re-alignment. The server has always sent it and the
 * local row discarded it, so neither the player nor an offline reader could
 * search the cached sync map for the page.
 */
class Migration22To23ExecutionTest {

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

    private fun insertBookmark(
        pairId: Int,
        updatedAt: String,
        capturedAt: String? = null,
        audioPositionMs: Int = 1_000,
        syncedToServer: Int = 1,
    ) {
        val captured = if (capturedAt == null) "NULL" else "'" + capturedAt + "'"
        exec(
            "INSERT INTO bookmarks (scopeKey, bookPairId, source, audioPositionMs, updatedAt, " +
                "syncedToServer, capturedAt) VALUES " +
                "('acct', $pairId, 'ebook', $audioPositionMs, '$updatedAt', $syncedToServer, " +
                captured + ")"
        )
    }

    private fun updatedAtOf(pairId: Int): String? =
        conn.createStatement()
            .executeQuery("SELECT updatedAt FROM bookmarks WHERE bookPairId = $pairId")
            .use { rs -> if (rs.next()) rs.getString("updatedAt") else null }

    @Test
    fun `an existing bookmark survives and gains an empty preview column`() {
        buildVersion(22)
        insertBookmark(1, updatedAt = "1788220800000", audioPositionMs = 123_456)

        MIGRATION_22_23.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM bookmarks").use { rs ->
            assertTrue("the row must survive", rs.next())
            assertEquals(123_456, rs.getInt("audioPositionMs"))
            assertEquals("1788220800000", rs.getString("updatedAt"))
            // Filled in by the next position pull, not by the migration. Until
            // then the chapter+sentence rung covers the gap.
            assertNull(rs.getString("epubTextPreview"))
        }
    }

    @Test
    fun `the migrated bookmarks table has exactly the columns Room expects at v23`() {
        buildVersion(22)

        MIGRATION_22_23.migrate(bridge())

        assertEquals(expectedColumns(23, "bookmarks").sorted(), columnsOf("bookmarks").sorted())
    }
}
