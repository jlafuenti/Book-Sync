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

/** v12 -> v13: book pair series index (from main). */
val MIGRATION_12_13 = object : Migration(12, 13) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE book_pairs ADD COLUMN ebookSeriesIndex REAL")
    }
}

/** v13 -> v14: sync point confidence + bookmark locatorAudioMs.
 *  Duplicate-tolerant: an interim test build added these columns at v13. */
val MIGRATION_13_14 = object : Migration(13, 14) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE sync_points ADD COLUMN confidence REAL NOT NULL DEFAULT 0")
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN locatorAudioMs INTEGER")
    }
}

/** v14 -> v15: multi-device conflict resolution (issue #54) — capturedAt/deviceId/deviceName
 *  on bookmarks, capturedAt/deviceName on user_progress (deviceId already existed there). */
val MIGRATION_14_15 = object : Migration(14, 15) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN capturedAt TEXT")
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN deviceId TEXT")
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN deviceName TEXT")
        addColumnIfMissing(db, "ALTER TABLE user_progress ADD COLUMN capturedAt TEXT")
        addColumnIfMissing(db, "ALTER TABLE user_progress ADD COLUMN deviceName TEXT")
    }
}

/** v15 -> v16: bookmark_log never got deviceId/deviceName in MIGRATION_14_15 — every
 *  History-tab entry silently lost device attribution regardless of what the server
 *  returned, since getBookmarkHistory always reads back through this local cache. */
val MIGRATION_15_16 = object : Migration(15, 16) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE bookmark_log ADD COLUMN deviceId TEXT")
        addColumnIfMissing(db, "ALTER TABLE bookmark_log ADD COLUMN deviceName TEXT")
    }
}
