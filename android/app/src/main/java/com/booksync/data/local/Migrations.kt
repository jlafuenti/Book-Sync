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

/** v16 -> v17: queued bookmark writes dropped locatorAudioMs (issue #40) — an offline
 *  save replayed a locator with no audio anchor, so the server (and any second device)
 *  couldn't tell whether that locator still described the current audio position. */
val MIGRATION_16_17 = object : Migration(16, 17) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE pending_sync ADD COLUMN locatorAudioMs INTEGER")
    }
}

/** v17 -> v18: record which sync-map version the cached sync points came from (issue #55).
 *  Re-transcription rebuilds the server's map with new audio timestamps, and nothing here
 *  ever compared versions — a cached map kept resolving text to seconds that no longer
 *  existed, so an ebook->audio jump landed in the wrong place forever.
 *
 *  Left NULL for existing rows: "version unknown". `refreshPairs` refetches those once — a
 *  cache we can't identify is exactly the state this fixes, so it is not assumed current. */
val MIGRATION_17_18 = object : Migration(17, 18) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE book_pairs ADD COLUMN syncMapVersion INTEGER")
    }
}

/** v18 -> v19: record which sync-map version a bookmark's own sentence index was resolved
 *  against (issue #116). The server used to stamp `bookmarks.sync_map_version` with whatever
 *  map was live when a write landed, so a deferred push of pre-re-transcription coordinates
 *  was recorded as current and nothing could detect the drift. The client now attests the
 *  version at resolution time; it rides on the bookmark row and on queued pending_sync rows
 *  so a later replay sends the version that was true when the index was computed.
 *
 *  Left NULL for existing rows: "unknown". The server stamps NULL for an unattested write
 *  rather than claiming currency for it. */
val MIGRATION_18_19 = object : Migration(18, 19) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(db, "ALTER TABLE bookmarks ADD COLUMN syncMapVersion INTEGER")
        addColumnIfMissing(db, "ALTER TABLE pending_sync ADD COLUMN syncMapVersion INTEGER")
    }
}

/**
 * v19 -> v20: scope the per-account tables to (server, user) — issue #314.
 *
 * `bookmarks`, `user_progress` and `acknowledged_items` are keyed on values unique
 * only *within* one account (`bookPairId`, `(mediaType, mediaId)`,
 * `(itemId, itemType)`), so two accounts on one device collided outright: one
 * user's upsert overwrote the other's row. SQLite cannot alter a primary key, so
 * those three are rebuilt. `pending_sync` and `bookmark_log` only need the column.
 *
 * Every existing row is copied with the empty legacy `scopeKey` and adopted once by
 * the first account to sign in afterwards. Copying rather than discarding is the
 * point: these tables hold `syncedToServer = 0` rows the server has never seen, and
 * on the single-account device this overwhelmingly runs on, adopting them is simply
 * correct.
 *
 * The CREATE statements are Room's own, copied from the generated
 * `BookSyncDatabase_Impl` for schema 20 — hand-writing them got the column list
 * wrong on the first attempt, which would have shifted values between columns.
 * Column lists are explicit rather than `SELECT *` so a future column added
 * without updating this fails loudly instead of silently misaligning.
 */
val MIGRATION_19_20 = object : Migration(19, 20) {
    override fun migrate(db: SupportSQLiteDatabase) {
        addColumnIfMissing(
            db, "ALTER TABLE pending_sync ADD COLUMN scopeKey TEXT NOT NULL DEFAULT ''"
        )
        addColumnIfMissing(
            db, "ALTER TABLE bookmark_log ADD COLUMN scopeKey TEXT NOT NULL DEFAULT ''"
        )

        // --- bookmarks ---
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `bookmarks_new` (`scopeKey` TEXT NOT NULL, `bookPairId` INTEGER NOT NULL, `source` TEXT NOT NULL, `epubChapter` INTEGER, `epubSentenceIndex` INTEGER, `audioPositionMs` INTEGER, `epubLocator` TEXT, `locatorAudioMs` INTEGER, `updatedAt` TEXT NOT NULL, `syncedToServer` INTEGER NOT NULL, `capturedAt` TEXT, `deviceId` TEXT, `deviceName` TEXT, `syncMapVersion` INTEGER, PRIMARY KEY(`scopeKey`, `bookPairId`))"
        )
        db.execSQL(
            "INSERT INTO bookmarks_new (scopeKey, bookPairId, source, epubChapter, epubSentenceIndex, audioPositionMs, epubLocator, locatorAudioMs, updatedAt, syncedToServer, capturedAt, deviceId, deviceName, syncMapVersion) " +
                "SELECT '', bookPairId, source, epubChapter, epubSentenceIndex, audioPositionMs, epubLocator, locatorAudioMs, updatedAt, syncedToServer, capturedAt, deviceId, deviceName, syncMapVersion FROM bookmarks"
        )
        db.execSQL("DROP TABLE bookmarks")
        db.execSQL("ALTER TABLE bookmarks_new RENAME TO bookmarks")

        // --- user_progress ---
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `user_progress_new` (`scopeKey` TEXT NOT NULL, `mediaType` TEXT NOT NULL, `mediaId` INTEGER NOT NULL, `bookPairId` INTEGER, `epubCfi` TEXT, `epubChapter` INTEGER, `epubProgressPercent` REAL, `audioPositionMs` INTEGER, `isCompleted` INTEGER NOT NULL, `updatedAt` INTEGER NOT NULL, `deviceId` TEXT, `syncedToServer` INTEGER NOT NULL, `capturedAt` TEXT, `deviceName` TEXT, PRIMARY KEY(`scopeKey`, `mediaType`, `mediaId`))"
        )
        db.execSQL(
            "INSERT INTO user_progress_new (scopeKey, mediaType, mediaId, bookPairId, epubCfi, epubChapter, epubProgressPercent, audioPositionMs, isCompleted, updatedAt, deviceId, syncedToServer, capturedAt, deviceName) " +
                "SELECT '', mediaType, mediaId, bookPairId, epubCfi, epubChapter, epubProgressPercent, audioPositionMs, isCompleted, updatedAt, deviceId, syncedToServer, capturedAt, deviceName FROM user_progress"
        )
        db.execSQL("DROP TABLE user_progress")
        db.execSQL("ALTER TABLE user_progress_new RENAME TO user_progress")

        // --- acknowledged_items ---
        db.execSQL(
            "CREATE TABLE IF NOT EXISTS `acknowledged_items_new` (`scopeKey` TEXT NOT NULL, `itemId` INTEGER NOT NULL, `itemType` TEXT NOT NULL, PRIMARY KEY(`scopeKey`, `itemId`, `itemType`))"
        )
        db.execSQL(
            "INSERT INTO acknowledged_items_new (scopeKey, itemId, itemType) " +
                "SELECT '', itemId, itemType FROM acknowledged_items"
        )
        db.execSQL("DROP TABLE acknowledged_items")
        db.execSQL("ALTER TABLE acknowledged_items_new RENAME TO acknowledged_items")
    }
}
