package com.booksync.data.local.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction

/**
 * The library cache, as a whole, for the owner check of issue #575.
 *
 * `book_pairs`, `ebooks` and `audiobooks` are a mirror of one server's
 * catalogue, and `sync_points` is alignment data derived from `book_pairs`.
 * Nothing in them is per-account — one Tandem's library is shared by every
 * account on it — so they were left out of the `(server, user)` partitioning of
 * issue #314. What they *are* is per-**server**: the ids are the server's own,
 * so the same row id means a different book on a different Tandem.
 *
 * That is what these statements clear, and only when the server changes.
 * Everything else the cache holds — `bookmarks`, `user_progress`,
 * `bookmark_log`, `pending_sync`, `acknowledged_items` — is deliberately out of
 * reach here: those rows are already scoped so the wrong account cannot read
 * them, and they carry `syncedToServer = 0` positions that exist nowhere else.
 * Deleting them to fix a library leak is the mistake #314 was filed to avoid,
 * and `LibraryCacheClearQueryTest` runs these statements to prove they do not.
 */
@Dao
interface LibraryCacheDao {

    @Query("DELETE FROM book_pairs")
    suspend fun clearPairs()

    @Query("DELETE FROM ebooks")
    suspend fun clearEbooks()

    @Query("DELETE FROM audiobooks")
    suspend fun clearAudiobooks()

    @Query("DELETE FROM sync_points")
    suspend fun clearSyncPoints()

    /**
     * All four together: a crash midway would otherwise leave, say, the pairs
     * gone and their sync points behind, where the next refresh against the new
     * server would resolve a pair id to another server's alignment data.
     */
    @Transaction
    suspend fun clearLibrary() {
        clearPairs()
        clearEbooks()
        clearAudiobooks()
        clearSyncPoints()
    }
}
