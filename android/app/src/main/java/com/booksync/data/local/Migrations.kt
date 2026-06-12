package com.booksync.data.local

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/** Add a column, ignoring the "duplicate column" error if it already exists
 *  (SQLite has no ADD COLUMN IF NOT EXISTS). */
private fun addColumnIfMissing(db: SupportSQLiteDatabase, sql: String) {
    try {
        db.execSQL(sql)
    } catch (e: android.database.sqlite.SQLiteException) {
        if (e.message?.contains("duplicate column", ignoreCase = true) != true) throw e
    }
}

/** v12 -> v13: sync point confidence + bookmark locatorAudioMs. */
val MIGRATION_12_13 = object : Migration(12, 13) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE sync_points ADD COLUMN confidence REAL NOT NULL DEFAULT 0")
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN locatorAudioMs INTEGER")
    }
}

/**
 * v13 -> v14: same columns, defensively. Some installed test builds were
 * already at DB version 13 with a different schema (no migration path), which
 * made Room's identity check crash at version 13. Bumping to 14 with
 * duplicate-tolerant ALTERs repairs both v12 and stray v13 databases.
 */
val MIGRATION_13_14 = object : Migration(13, 14) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE sync_points ADD COLUMN confidence REAL NOT NULL DEFAULT 0")
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN locatorAudioMs INTEGER")
    }
}
