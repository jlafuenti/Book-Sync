package com.booksync.data.local.dao

import androidx.room.*
import com.booksync.data.local.entity.*
import kotlinx.coroutines.flow.Flow

/**
 * Room DAOs for local database operations.
 */

@Dao
interface BookPairDao {
    @Query("SELECT * FROM book_pairs ORDER BY ebookTitle")
    fun getAllPairs(): Flow<List<BookPairEntity>>

    @Query("SELECT * FROM book_pairs WHERE id = :pairId")
    suspend fun getPairById(pairId: Int): BookPairEntity?

    @Upsert
    suspend fun upsertPairs(pairs: List<BookPairEntity>)

    @Upsert
    suspend fun upsertPair(pair: BookPairEntity)

    @Query("UPDATE book_pairs SET ebookDownloaded = :downloaded WHERE id = :pairId")
    suspend fun setEbookDownloaded(pairId: Int, downloaded: Boolean)

    @Query("UPDATE book_pairs SET audiobookDownloaded = :downloaded WHERE id = :pairId")
    suspend fun setAudiobookDownloaded(pairId: Int, downloaded: Boolean)

    @Query("UPDATE book_pairs SET syncMapDownloaded = :downloaded WHERE id = :pairId")
    suspend fun setSyncMapDownloaded(pairId: Int, downloaded: Boolean)

    @Delete
    suspend fun deletePair(pair: BookPairEntity)
}

@Dao
interface SyncPointDao {
    @Query("SELECT * FROM sync_points WHERE bookPairId = :pairId ORDER BY epubChapter, epubSentenceIndex")
    suspend fun getPointsForPair(pairId: Int): List<SyncPointEntity>

    @Query("SELECT * FROM sync_points WHERE bookPairId = :pairId ORDER BY epubChapter, epubSentenceIndex")
    fun getPointsForPairFlow(pairId: Int): Flow<List<SyncPointEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertPoints(points: List<SyncPointEntity>)

    @Query("DELETE FROM sync_points WHERE bookPairId = :pairId")
    suspend fun deletePointsForPair(pairId: Int)
}

@Dao
interface BookmarkDao {
    @Query("SELECT * FROM bookmarks WHERE bookPairId = :pairId")
    suspend fun getBookmark(pairId: Int): BookmarkEntity?

    @Query("SELECT * FROM bookmarks WHERE bookPairId = :pairId")
    fun getBookmarkFlow(pairId: Int): Flow<BookmarkEntity?>

    @Upsert
    suspend fun upsertBookmark(bookmark: BookmarkEntity)

    @Query("UPDATE bookmarks SET syncedToServer = 0 WHERE bookPairId = :pairId")
    suspend fun markUnsynced(pairId: Int)
}

@Dao
interface PendingSyncDao {
    @Query("SELECT * FROM pending_sync ORDER BY createdAt ASC")
    suspend fun getAllPending(): List<PendingSyncEntity>

    @Insert
    suspend fun insert(sync: PendingSyncEntity)

    @Delete
    suspend fun delete(sync: PendingSyncEntity)

    @Query("DELETE FROM pending_sync")
    suspend fun deleteAll()
}

@Dao
interface UserProgressDao {
    @Query("SELECT * FROM user_progress WHERE mediaType = :mediaType AND mediaId = :mediaId")
    suspend fun getProgress(mediaType: String, mediaId: Int): UserProgressEntity?

    @Query("SELECT * FROM user_progress WHERE mediaType = :mediaType AND mediaId = :mediaId")
    fun getProgressFlow(mediaType: String, mediaId: Int): Flow<UserProgressEntity?>

    @Query("SELECT * FROM user_progress")
    fun getAllProgressFlow(): Flow<List<UserProgressEntity>>

    @Upsert
    suspend fun upsertProgress(progress: UserProgressEntity)

    @Query("UPDATE user_progress SET syncedToServer = 0 WHERE mediaType = :mediaType AND mediaId = :mediaId")
    suspend fun markUnsynced(mediaType: String, mediaId: Int)

    @Query("SELECT * FROM user_progress WHERE syncedToServer = 0")
    suspend fun getUnsyncedProgress(): List<UserProgressEntity>
}
