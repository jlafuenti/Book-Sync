package com.booksync.data.local.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction

/**
 * One-time adoption of rows written before the cache was scoped (issue #314).
 *
 * This is what replaces "clear the previous user's data on a switch". Clearing was
 * the wrong primitive: `bookmarks` and `user_progress` carry `syncedToServer = 0`
 * rows the server has never seen, so deleting them destroys reading positions
 * outright. Partitioning keeps every row and simply stops the wrong account
 * reading — or replaying — them.
 *
 * Legacy rows are claimed by the first account to sign in after the upgrade. On the
 * single-account device this overwhelmingly runs on that is exactly right, and it
 * is the only option that does not discard unsynced positions. Once claimed the
 * legacy scope is empty forever; nothing writes it again.
 */
@Dao
interface ScopeAdoptionDao {

    @Query("UPDATE bookmarks SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptBookmarks(scope: String)

    @Query("UPDATE user_progress SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptProgress(scope: String)

    @Query("UPDATE bookmark_log SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptBookmarkLog(scope: String)

    @Query("UPDATE acknowledged_items SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptAcknowledged(scope: String)

    @Query("UPDATE pending_sync SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptPendingSync(scope: String)

    @Query("SELECT EXISTS(SELECT 1 FROM bookmarks WHERE scopeKey = '' LIMIT 1)")
    suspend fun hasLegacyBookmarks(): Boolean

    /**
     * All five in one transaction: a crash midway would otherwise leave half the
     * rows claimed and half stranded in a scope nothing reads again.
     */
    @Transaction
    suspend fun adoptAll(scope: String) {
        adoptBookmarks(scope)
        adoptProgress(scope)
        adoptBookmarkLog(scope)
        adoptAcknowledged(scope)
        adoptPendingSync(scope)
    }
}
