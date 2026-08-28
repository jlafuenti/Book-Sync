package com.booksync.data.local

import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins `MIGRATION_19_20`'s table rebuilds against Room's own exported schemas.
 *
 * Issue #314 widened three primary keys, and SQLite cannot alter a primary key, so
 * `bookmarks`, `user_progress` and `acknowledged_items` are dropped and recreated
 * with the user's real reading positions copied across. The first draft of that
 * migration was hand-written and **wrong** — it invented `lastSource` and
 * `locatorStale`, and omitted `source`, `capturedAt`, `deviceId` and `deviceName`.
 * On a real device that shifts values between columns.
 *
 * There is no `androidTest` source set and no `room-testing` dependency here, so a
 * `MigrationTestHelper` test is not available; this reads the migration source and
 * the committed schema JSON instead. It cannot prove the migration *runs*, but it
 * does pin the two mistakes that are easy to make and invisible in review:
 *
 *  - a `CREATE TABLE` that does not match what Room expects at v20, and
 *  - an `INSERT`/`SELECT` pair whose column lists disagree, or that silently drops
 *    a column that existed at v19.
 */
class MigrationSchemaContractTest {

    private val rebuilt = listOf("bookmarks", "user_progress", "acknowledged_items")

    private fun schemaDir(): File {
        var dir = File("").absoluteFile
        repeat(4) {
            for (c in listOf(
                File(dir, "app/schemas/com.booksync.data.local.BookSyncDatabase"),
                File(dir, "schemas/com.booksync.data.local.BookSyncDatabase"),
            )) if (c.isDirectory) return c
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("exported schemas not found from ${File("").absolutePath}")
    }

    /**
     * Deliberately kotlinx-serialization, not `org.json`: the latter is the stubbed
     * Android class in JVM unit tests and returns null under
     * `unitTests.isReturnDefaultValues`, so the parse would quietly do nothing.
     */
    private fun createSql(version: Int, table: String): String {
        val root = Json.parseToJsonElement(File(schemaDir(), "$version.json").readText())
        val entities = root.jsonObject["database"]!!.jsonObject["entities"]!!.jsonArray
        for (e in entities) {
            val o = e.jsonObject
            if (o["tableName"]!!.jsonPrimitive.content == table) {
                return o["createSql"]!!.jsonPrimitive.content
            }
        }
        throw AssertionError("$table not found in $version.json")
    }

    private fun columns(createSql: String): List<String> =
        Regex("""`(\w+)`\s+(?:INTEGER|TEXT|REAL|BLOB)""")
            .findAll(createSql).map { it.groupValues[1] }.toList()

    private fun migrationSource(): String {
        var dir = File("").absoluteFile
        repeat(4) {
            for (c in listOf(
                File(dir, "app/src/main/java/com/booksync/data/local/Migrations.kt"),
                File(dir, "src/main/java/com/booksync/data/local/Migrations.kt"),
            )) if (c.exists()) return c.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Migrations.kt not found")
    }

    @Test
    fun `each rebuilt table is created with exactly the columns Room expects at v20`() {
        val src = migrationSource()
        for (table in rebuilt) {
            val expected = columns(createSql(20, table))
            val stmt = Regex("""CREATE TABLE IF NOT EXISTS `${table}_new` \(([^"]*)\)""")
                .find(src)?.groupValues?.get(1)
                ?: throw AssertionError("no CREATE for ${table}_new in MIGRATION_19_20")
            assertEquals(
                "MIGRATION_19_20's ${table}_new does not match schema 20 — a mismatch " +
                    "here shifts real reading positions between columns",
                expected, columns(stmt),
            )
        }
    }

    @Test
    fun `the copy carries every column that existed at v19`() {
        val src = migrationSource()
        for (table in rebuilt) {
            val v19 = columns(createSql(19, table)).toSet()
            val insert = Regex("""INSERT INTO ${table}_new \(scopeKey, ([^)]*)\)""")
                .find(src)?.groupValues?.get(1)
                ?: throw AssertionError("no INSERT for ${table}_new")
            val copied = insert.split(",").map { it.trim() }.filter { it.isNotEmpty() }.toSet()
            val dropped = v19 - copied
            assertTrue(
                "MIGRATION_19_20 drops $dropped from $table — those rows are the " +
                    "user's positions, and the data is gone once the old table is",
                dropped.isEmpty(),
            )
        }
    }

    @Test
    fun `the insert and select column lists agree positionally`() {
        // The failure this guards is silent: SQLite happily copies values into the
        // wrong columns if the two lists differ, and the old table is dropped
        // immediately afterwards.
        val src = migrationSource()
        for (table in rebuilt) {
            val insert = Regex("""INSERT INTO ${table}_new \(scopeKey, ([^)]*)\)""")
                .find(src)?.groupValues?.get(1)
                ?: throw AssertionError("no INSERT for ${table}_new")
            val select = Regex("""SELECT '', (.*?) FROM $table"""")
                .find(src)?.groupValues?.get(1)
                ?: throw AssertionError("no SELECT for $table")
            fun cols(x: String) = x.split(",")
                .map { it.trim().trim('"').trim() }
                .filter { it.isNotEmpty() && it != "+" }
            assertEquals(
                "INSERT and SELECT disagree for $table — values would land in the " +
                    "wrong columns and the old table is dropped straight after",
                cols(insert), cols(select),
            )
        }
    }
}
