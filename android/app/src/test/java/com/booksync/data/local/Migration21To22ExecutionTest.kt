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
 * Executes [MIGRATION_21_22] — normalising `bookmarks.updatedAt` to one shape
 * (issue #617) — against a populated v21 database built from the committed
 * 21.json, the same JDBC bridge as [Migration20To21ExecutionTest].
 *
 * The column used to hold whichever shape wrote it last: an epoch-millis string
 * from a local save, the server's ISO datetime from a pull. `getRecentlyPlayedPairs`
 * sorts it as TEXT, where every `'2026-...'` outranks every `'17...'`. The mapper
 * now normalises on ingest; this rewrites the rows already on disk.
 */
class Migration21To22ExecutionTest {

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
    fun `an ISO updatedAt becomes epoch millis`() {
        buildVersion(21)
        // The exact shape FastAPI serialises a naive-UTC datetime to.
        insertBookmark(1, updatedAt = "2026-08-17T00:18:40.003994")

        MIGRATION_21_22.migrate(bridge())

        assertEquals("1786925920003", updatedAtOf(1))
    }

    @Test
    fun `an ISO updatedAt with no fractional part becomes epoch millis`() {
        buildVersion(21)
        insertBookmark(1, updatedAt = "2026-08-17T00:18:40")

        MIGRATION_21_22.migrate(bridge())

        assertEquals("1786925920000", updatedAtOf(1))
    }

    @Test
    fun `a short fractional part is padded, not truncated`() {
        // "...40.5" is 500 ms, not 5.
        buildVersion(21)
        insertBookmark(1, updatedAt = "2026-08-17T00:18:40.5")

        MIGRATION_21_22.migrate(bridge())

        assertEquals("1786925920500", updatedAtOf(1))
    }

    @Test
    fun `a zone-suffixed ISO updatedAt is read as UTC`() {
        // The server emits naive UTC, but a Z suffix has reached this column and
        // parseSyncTimestamp has always read it as UTC.
        buildVersion(21)
        insertBookmark(1, updatedAt = "2026-08-17T00:18:40Z")

        MIGRATION_21_22.migrate(bridge())

        assertEquals("1786925920000", updatedAtOf(1))
    }

    @Test
    fun `an epoch-millis updatedAt is left exactly as it was`() {
        buildVersion(21)
        insertBookmark(1, updatedAt = "1788220800000")

        MIGRATION_21_22.migrate(bridge())

        assertEquals("1788220800000", updatedAtOf(1))
    }

    @Test
    fun `a value that is neither shape is left alone rather than zeroed`() {
        // Nothing should ever have written this, and rewriting it to "0" would
        // silently sink the row to the bottom of Continue Listening. Leaving it
        // costs nothing: every Kotlin consumer already scores it 0.
        buildVersion(21)
        insertBookmark(1, updatedAt = "garbage")

        MIGRATION_21_22.migrate(bridge())

        assertEquals("garbage", updatedAtOf(1))
    }

    @Test
    fun `the rest of the row survives untouched`() {
        buildVersion(21)
        insertBookmark(
            1,
            updatedAt = "2026-08-17T00:18:40.003994",
            capturedAt = "2026-08-17T00:18:39.000000",
            audioPositionMs = 123_456,
            syncedToServer = 0,
        )

        MIGRATION_21_22.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM bookmarks").use { rs ->
            assertTrue("the row must survive", rs.next())
            assertEquals(123_456, rs.getInt("audioPositionMs"))
            // The capture moment keeps the server's own shape: it is what
            // conflict resolution adjudicates on, and nothing sorts it as text.
            assertEquals("2026-08-17T00:18:39.000000", rs.getString("capturedAt"))
            // An unsynced row must still be unsynced — the offline queue
            // depends on it.
            assertEquals(0, rs.getInt("syncedToServer"))
            assertNull(rs.getString("deviceId"))
        }
    }

    @Test
    fun `after the migration the whole table sorts by recency as text`() {
        // The point of the change: this is the DESC TEXT sort
        // getRecentlyPlayedPairs performs.
        buildVersion(21)
        insertBookmark(1, updatedAt = "2026-08-17T00:18:40.003994") // older
        insertBookmark(2, updatedAt = "1788220800000") // newer

        MIGRATION_21_22.migrate(bridge())

        val order = conn.createStatement()
            .executeQuery("SELECT bookPairId FROM bookmarks ORDER BY updatedAt DESC")
            .use { rs -> buildList { while (rs.next()) add(rs.getInt(1)) } }
        assertEquals(listOf(2, 1), order)
    }

    @Test
    fun `the migrated bookmarks table has exactly the columns Room expects at v22`() {
        buildVersion(21)

        MIGRATION_21_22.migrate(bridge())

        assertEquals(expectedColumns(22, "bookmarks").sorted(), columnsOf("bookmarks").sorted())
    }
}
