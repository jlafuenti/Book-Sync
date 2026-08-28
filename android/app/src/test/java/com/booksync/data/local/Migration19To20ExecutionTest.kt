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
 * Executes [MIGRATION_19_20] against a real, populated v19 database.
 *
 * Issue #314 widened three primary keys, and SQLite cannot alter one, so
 * `bookmarks`, `user_progress` and `acknowledged_items` are dropped and recreated
 * with the reading positions copied across. Until this test the migration was
 * verified by review alone — and its first draft was wrong: it invented
 * `lastSource` and `locatorStale` and omitted `source`, `capturedAt`, `deviceId`
 * and `deviceName`, which would have shifted real positions between columns.
 *
 * Room's MigrationTestHelper needs an instrumentation context, and this module has
 * no androidTest source set, so a test written that way would never run in CI.
 * This drives the real [MIGRATION_19_20] object — not a copy of its SQL — through
 * a SupportSQLiteDatabase that forwards execSQL to in-memory SQLite over JDBC.
 *
 * The v19 fixture is built from the committed 19.json, so it cannot drift from
 * what devices in the field actually have.
 */
class Migration19To20ExecutionTest {

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

    /** A SupportSQLiteDatabase that only knows how to run statements. */
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

    private fun createStatements(version: Int): List<String> {
        val placeholder = "$" + "{TABLE_NAME}"
        return entities(version).map { e ->
            val o = e.jsonObject
            o["createSql"]!!.jsonPrimitive.content
                .replace(placeholder, o["tableName"]!!.jsonPrimitive.content)
        }
    }

    /** Column names Room declares for a table, in declaration order. */
    private fun expectedColumns(version: Int, table: String): List<String> =
        entities(version).map { it.jsonObject }
            .first { it["tableName"]!!.jsonPrimitive.content == table }["fields"]!!
            .jsonArray.map { it.jsonObject["columnName"]!!.jsonPrimitive.content }

    private fun buildV19() = createStatements(19).forEach { exec(it) }

    private fun columnsOf(table: String): List<String> =
        conn.createStatement().executeQuery("PRAGMA table_info(`$table`)").use { rs ->
            buildList { while (rs.next()) add(rs.getString("name")) }
        }

    private fun rowCount(table: String): Int =
        conn.createStatement().executeQuery("SELECT COUNT(*) FROM `$table`").use {
            it.next(); it.getInt(1)
        }

    @Test
    fun `a populated v19 bookmark migrates with every value in the right column`() {
        buildV19()
        exec(
            "INSERT INTO bookmarks (bookPairId, source, epubChapter, epubSentenceIndex, " +
                "audioPositionMs, epubLocator, locatorAudioMs, updatedAt, syncedToServer, " +
                "capturedAt, deviceId, deviceName, syncMapVersion) VALUES " +
                "(42, 'ebook', 7, 13, 1234, 'loc', 99, '2026-08-01T00:00:00Z', 0, " +
                "'2026-08-01T00:00:01Z', 'device-abc', 'Pixel', 3)"
        )

        MIGRATION_19_20.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM bookmarks").use { rs ->
            assertTrue("the row must survive the rebuild", rs.next())
            assertEquals("", rs.getString("scopeKey"))
            assertEquals(42, rs.getInt("bookPairId"))
            // The four columns the hand-written draft dropped:
            assertEquals("ebook", rs.getString("source"))
            assertEquals("2026-08-01T00:00:01Z", rs.getString("capturedAt"))
            assertEquals("device-abc", rs.getString("deviceId"))
            assertEquals("Pixel", rs.getString("deviceName"))
            // ...and ones a shifted column list would have scrambled:
            assertEquals(7, rs.getInt("epubChapter"))
            assertEquals(13, rs.getInt("epubSentenceIndex"))
            assertEquals(1234, rs.getInt("audioPositionMs"))
            assertEquals(99, rs.getInt("locatorAudioMs"))
            assertEquals(3, rs.getInt("syncMapVersion"))
            // An unsynced row is a position the server has never seen; losing the
            // flag strands it, because the unsynced sweep would skip it forever.
            assertEquals(0, rs.getInt("syncedToServer"))
        }
    }

    @Test
    fun `standalone progress survives, including the unsynced flag`() {
        // The audiobook-only listener: rows here and nothing in bookmarks.
        buildV19()
        exec(
            "INSERT INTO user_progress (mediaType, mediaId, audioPositionMs, isCompleted, " +
                "updatedAt, deviceId, syncedToServer, capturedAt, deviceName) VALUES " +
                "('audiobook', 17, 987654, 0, 1756000000000, 'device-abc', 0, " +
                "'2026-08-01T00:00:02Z', 'Pixel')"
        )

        MIGRATION_19_20.migrate(bridge())

        conn.createStatement().executeQuery("SELECT * FROM user_progress").use { rs ->
            assertTrue(rs.next())
            assertEquals("", rs.getString("scopeKey"))
            assertEquals("audiobook", rs.getString("mediaType"))
            assertEquals(17, rs.getInt("mediaId"))
            assertEquals(987654, rs.getInt("audioPositionMs"))
            assertEquals(0, rs.getInt("syncedToServer"))
            assertEquals("2026-08-01T00:00:02Z", rs.getString("capturedAt"))
        }
    }

    @Test
    fun `every migrated table has exactly the columns Room expects at v20`() {
        // A missing or extra column here is what makes Room throw at open time,
        // and the app then cannot start at all.
        //
        // Membership, not order: Room compares `TableInfo.columns`, which is a
        // Map keyed by name (verified in room-runtime 2.8.4), so ordering is not
        // part of the contract. It cannot be — `ALTER TABLE ADD COLUMN` can only
        // append, so `pending_sync` and `bookmark_log` necessarily end up with
        // `scopeKey` last while the entity declares it second.
        buildV19()
        MIGRATION_19_20.migrate(bridge())

        for (table in listOf(
            "bookmarks", "user_progress", "acknowledged_items", "pending_sync", "bookmark_log",
        )) {
            assertEquals(
                "$table does not match schema 20 after migrating",
                expectedColumns(20, table).sorted(), columnsOf(table).sorted(),
            )
        }
    }

    @Test
    fun `the rebuilt tables match Room's column order too`() {
        // Stricter than Room requires, and deliberately so: these three are
        // created from a CREATE TABLE written out by hand in the migration, so
        // matching Room's own declaration order exactly is free evidence that the
        // statement was copied rather than reconstructed from memory — which is
        // how the first draft ended up with invented columns.
        buildV19()
        MIGRATION_19_20.migrate(bridge())

        for (table in listOf("bookmarks", "user_progress", "acknowledged_items")) {
            assertEquals(
                "$table column order drifted from schema 20",
                expectedColumns(20, table), columnsOf(table),
            )
        }
    }

    @Test
    fun `no rows are lost across any rebuilt table`() {
        buildV19()
        repeat(3) { i ->
            exec(
                "INSERT INTO bookmarks (bookPairId, source, updatedAt, syncedToServer) " +
                    "VALUES ($i, 'ebook', '2026-08-01T00:00:00Z', 1)"
            )
            exec(
                "INSERT INTO user_progress (mediaType, mediaId, isCompleted, updatedAt, " +
                    "syncedToServer) VALUES ('audiobook', $i, 0, 1756000000000, 1)"
            )
            exec("INSERT INTO acknowledged_items (itemId, itemType) VALUES ($i, 'ebook')")
            exec(
                "INSERT INTO pending_sync (bookPairId, source, appendToLog, createdAt) " +
                    "VALUES ($i, 'ebook', 0, 1756000000000)"
            )
        }

        MIGRATION_19_20.migrate(bridge())

        for (table in listOf("bookmarks", "user_progress", "acknowledged_items", "pending_sync")) {
            assertEquals("$table lost rows in the rebuild", 3, rowCount(table))
        }
    }
}
