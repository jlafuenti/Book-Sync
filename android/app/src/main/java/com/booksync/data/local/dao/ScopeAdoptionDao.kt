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

    @Query("UPDATE OR IGNORE bookmarks SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptBookmarks(scope: String)

    @Query("DELETE FROM bookmarks WHERE scopeKey = ''")
    suspend fun dropLeftoverBookmarks()

    @Query("UPDATE OR IGNORE user_progress SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptProgress(scope: String)

    @Query("DELETE FROM user_progress WHERE scopeKey = ''")
    suspend fun dropLeftoverProgress()

    @Query("UPDATE OR IGNORE bookmark_log SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptBookmarkLog(scope: String)

    @Query("DELETE FROM bookmark_log WHERE scopeKey = ''")
    suspend fun dropLeftoverBookmarkLog()

    @Query("UPDATE OR IGNORE acknowledged_items SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptAcknowledged(scope: String)

    @Query("DELETE FROM acknowledged_items WHERE scopeKey = ''")
    suspend fun dropLeftoverAcknowledged()

    @Query("UPDATE OR IGNORE pending_sync SET scopeKey = :scope WHERE scopeKey = ''")
    suspend fun adoptPendingSync(scope: String)

    @Query("DELETE FROM pending_sync WHERE scopeKey = ''")
    suspend fun dropLeftoverPendingSync()

    /**
     * All five in one transaction: a crash midway would otherwise leave half the
     * rows claimed and half stranded in a scope nothing reads again.
     *
     * Unconditional — there is deliberately no "are there legacy rows?" check.
     * Gating on one table strands the others: a standalone-audiobook listener has
     * `user_progress` rows and an empty `bookmarks`, so a bookmarks-shaped gate
     * would leave their positions invisible and unsyncable forever. Five indexed
     * UPDATEs that match nothing cost nothing after the first run, and removing
     * the check removes the whole class of "gated on the wrong table".
     *
     * `UPDATE OR IGNORE`, not `UPDATE`, because the scope is part of the primary
     * key on three of these tables. `AudioPlayerService` is a `MediaLibraryService`
     * that Android Auto and system media resumption start with no Activity alive,
     * so it can migrate the database and write scoped rows before anything calls
     * this. A plain UPDATE then hits a PK collision, the transaction rolls back so
     * *nothing* is adopted, and it fails the same way on every launch afterwards.
     *
     * Where both exist, the scoped row wins and the legacy duplicate is dropped:
     * it was written after the migration, so it is the more recent of the two.
     */
    @Transaction
    suspend fun adoptAll(scope: String) {
        adoptBookmarks(scope)
        adoptProgress(scope)
        adoptBookmarkLog(scope)
        adoptAcknowledged(scope)
        adoptPendingSync(scope)
        // Anything OR IGNORE skipped is a duplicate of a row this account already
        // has; leaving it would keep it invisible in a scope nothing reads.
        dropLeftoverBookmarks()
        dropLeftoverProgress()
        dropLeftoverBookmarkLog()
        dropLeftoverAcknowledged()
        dropLeftoverPendingSync()
    }
}
