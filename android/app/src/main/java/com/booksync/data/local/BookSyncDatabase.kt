package com.booksync.data.local

import androidx.room.Database
import androidx.room.RoomDatabase
import com.booksync.data.local.dao.*
import com.booksync.data.local.entity.*

/**
 * Room database for BookSync local storage.
 * Stores book pairs, sync maps, bookmarks, and pending sync queue.
 */
@Database(
    entities = [
        BookPairEntity::class,
        EBookEntity::class,
        AudioBookEntity::class,
        SyncPointEntity::class,
        BookmarkEntity::class,
        PendingSyncEntity::class,
        UserProgressEntity::class,
        AcknowledgedItemEntity::class,
        BookmarkLogEntity::class
    ],
    version = 20,
    // Exported to app/schemas/ (room.schemaLocation in build.gradle.kts) so
    // schema changes show up in diffs and MigrationTestHelper tests become
    // possible (issue #168). Commit the generated JSON with every version bump.
    exportSchema = true
)
abstract class BookSyncDatabase : RoomDatabase() {
    abstract fun bookPairDao(): BookPairDao
    abstract fun eBookDao(): EBookDao
    abstract fun audioBookDao(): AudioBookDao
    abstract fun syncPointDao(): SyncPointDao
    abstract fun bookmarkDao(): BookmarkDao
    abstract fun pendingSyncDao(): PendingSyncDao
    abstract fun userProgressDao(): UserProgressDao
    abstract fun acknowledgedItemDao(): AcknowledgedItemDao
    abstract fun bookmarkLogDao(): BookmarkLogDao
    abstract fun scopeAdoptionDao(): ScopeAdoptionDao
}
