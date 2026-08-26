package com.booksync.data.local

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Guards for issue #168: the database ships with hand-written migrations and
 * used to pair them with a blanket `fallbackToDestructiveMigration(dropAllTables
 * = true)` — so one future version bump without a migration would silently
 * drop EVERY table, including unsynced `bookmarks`/`user_progress` rows (the
 * positions the server has never seen) and the whole `pending_sync` offline
 * queue. That mistake has already happened once: MIGRATION_15_16 exists to
 * repair columns MIGRATION_14_15 forgot.
 *
 * These parse the source (SyncWiringTest precedent — no Robolectric or
 * room-testing here) and pin:
 *  - the migration chain is unbroken and ends at the declared version, so a
 *    version bump without a Migration(N-1, N) fails this test instead of
 *    wiping user data;
 *  - every declared migration is actually registered in AppModule;
 *  - the blanket destructive fallback is gone — only the bounded
 *    `fallbackToDestructiveMigrationFrom(...)` for pre-position-work versions
 *    (< 12) remains;
 *  - the schema is exported, so migrations are reviewable in diffs and a
 *    MigrationTestHelper test can be written when the version next bumps.
 */
class MigrationCoverageTest {

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    @Test
    fun `the migration chain is unbroken and ends at the declared version`() {
        val version = Regex("version = (\\d+)")
            .find(source("com/booksync/data/local/BookSyncDatabase.kt"))
            ?.groupValues?.get(1)?.toInt()
            ?: throw AssertionError("version = N not found in BookSyncDatabase.kt")

        val pairs = Regex("Migration\\((\\d+),\\s*(\\d+)\\)")
            .findAll(source("com/booksync/data/local/Migrations.kt"))
            .map { it.groupValues[1].toInt() to it.groupValues[2].toInt() }
            .sortedBy { it.first }
            .toList()

        assertTrue("no migrations found", pairs.isNotEmpty())
        for ((from, to) in pairs) {
            assertEquals("migration $from must step by exactly one", from + 1, to)
        }
        for (i in 1 until pairs.size) {
            assertEquals(
                "gap in the migration chain after ${pairs[i - 1].second}",
                pairs[i - 1].second, pairs[i].first,
            )
        }
        assertEquals(
            "BookSyncDatabase.version was bumped without a Migration(${version - 1}, $version) — " +
                "without one, the destructive fallback would have wiped every table (issue #168)",
            version, pairs.last().second,
        )
    }

    @Test
    fun `every declared migration is registered in AppModule`() {
        val names = Regex("val (MIGRATION_\\d+_\\d+)")
            .findAll(source("com/booksync/data/local/Migrations.kt"))
            .map { it.groupValues[1] }.toList()
        val appModule = source("com/booksync/di/AppModule.kt")

        assertTrue("no MIGRATION_ vals found", names.isNotEmpty())
        for (name in names) {
            assertTrue(
                "$name is declared but not passed to addMigrations(...) in AppModule — " +
                    "an unregistered migration is a missing migration at runtime",
                appModule.contains(name),
            )
        }
    }

    @Test
    fun `the blanket destructive fallback is gone`() {
        val lines = source("com/booksync/di/AppModule.kt")
            .lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }
        assertTrue(
            "AppModule must not use the blanket fallbackToDestructiveMigration(...) — " +
                "only the version-bounded fallbackToDestructiveMigrationFrom(...) for " +
                "pre-position-work installs (issue #168)",
            lines.none {
                it.contains("fallbackToDestructiveMigration(")
            },
        )
    }

    @Test
    fun `the schema is exported so migrations are reviewable`() {
        assertTrue(
            "BookSyncDatabase must set exportSchema = true (with room.schemaLocation " +
                "in build.gradle.kts) — without it no MigrationTestHelper test can " +
                "ever be written and schema changes are invisible in diffs",
            source("com/booksync/data/local/BookSyncDatabase.kt").contains("exportSchema = true"),
        )
    }
}
