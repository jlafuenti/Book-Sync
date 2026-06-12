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

    @Query("SELECT * FROM book_pairs WHERE ebookDownloaded = 1 OR audiobookDownloaded = 1 ORDER BY ebookTitle")
    fun getDownloadedPairs(): Flow<List<BookPairEntity>>

    @Query("SELECT * FROM book_pairs WHERE id = :pairId")
    suspend fun getPairById(pairId: Int): BookPairEntity?

    @Query("SELECT * FROM book_pairs WHERE id = :pairId")
    fun getPairByIdFlow(pairId: Int): Flow<BookPairEntity?>

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

    @Query("DELETE FROM book_pairs WHERE id = :pairId")
    suspend fun deletePairById(pairId: Int)

    @Query("DELETE FROM book_pairs WHERE id NOT IN (:ids)")
    suspend fun deleteOrphansExcept(ids: List<Int>)

    @Query("DELETE FROM book_pairs")
    suspend fun deleteAll()

    @Query("SELECT * FROM book_pairs ORDER BY ebookTitle")
    suspend fun getAllPairsOnce(): List<BookPairEntity>

    /**
     * Pairs with audio progress, ordered by most recently listened.
     * NOTE: bookmarks.updatedAt is stored as a numeric epoch-ms string
     * (e.g. "1741910592000"). String DESC sort is correct for fixed-length
     * numeric strings — do not change to ISO datetime format without updating this query.
     */
    @Query("""
        SELECT bp.* FROM book_pairs bp
        INNER JOIN bookmarks b ON bp.id = b.bookPairId
        WHERE bp.audiobookDownloaded = 1
          AND b.audioPositionMs > 0
        ORDER BY b.updatedAt DESC
    """)
    fun getRecentlyPlayedPairs(): Flow<List<BookPairEntity>>
}

@Dao
interface EBookDao {
    @Query("SELECT * FROM ebooks ORDER BY title")
    fun getAllEBooks(): Flow<List<EBookEntity>>

    @Query("SELECT * FROM ebooks WHERE isDownloaded = 1 ORDER BY title")
    fun getDownloadedEBooks(): Flow<List<EBookEntity>>

    @Query("SELECT * FROM ebooks WHERE id = :ebookId")
    suspend fun getEBookById(ebookId: Int): EBookEntity?

    @Query("SELECT * FROM ebooks WHERE id = :ebookId")
    fun getEBookByIdFlow(ebookId: Int): Flow<EBookEntity?>

    @Upsert
    suspend fun upsertEBooks(ebooks: List<EBookEntity>)

    @Query("DELETE FROM ebooks WHERE id NOT IN (:ids)")
    suspend fun deleteOrphansExcept(ids: List<Int>)

    @Query("DELETE FROM ebooks")
    suspend fun deleteAll()

    @Query("UPDATE ebooks SET isDownloaded = :downloaded WHERE id = :ebookId")
    suspend fun setDownloaded(ebookId: Int, downloaded: Boolean)

    @Query("SELECT * FROM ebooks ORDER BY title")
    suspend fun getAllEBooksOnce(): List<EBookEntity>

    /**
     * Ebooks with reading progress, ordered by most recently read.
     * Joins user_progress; updatedAt is a Long epoch-ms timestamp.
     */
    @Query("""
        SELECT eb.* FROM ebooks eb
        INNER JOIN user_progress up ON up.mediaType = 'ebook' AND up.mediaId = eb.id
        WHERE eb.isDownloaded = 1
          AND up.isCompleted = 0
          AND (up.epubProgressPercent IS NOT NULL AND up.epubProgressPercent > 0
               OR up.epubChapter IS NOT NULL AND up.epubChapter > 0)
        ORDER BY up.updatedAt DESC
    """)
    fun getRecentlyReadEbooks(): Flow<List<EBookEntity>>
}

@Dao
interface AudioBookDao {
    @Query("SELECT * FROM audiobooks ORDER BY title")
    fun getAllAudioBooks(): Flow<List<AudioBookEntity>>

    @Query("SELECT * FROM audiobooks WHERE isDownloaded = 1 ORDER BY title")
    fun getDownloadedAudioBooks(): Flow<List<AudioBookEntity>>

    @Query("SELECT * FROM audiobooks WHERE id = :audiobookId")
    suspend fun getAudioBookById(audiobookId: Int): AudioBookEntity?

    @Query("SELECT * FROM audiobooks WHERE id = :audiobookId")
    fun getAudioBookByIdFlow(audiobookId: Int): Flow<AudioBookEntity?>

    @Upsert
    suspend fun upsertAudioBooks(audiobooks: List<AudioBookEntity>)

    @Query("DELETE FROM audiobooks WHERE id NOT IN (:ids)")
    suspend fun deleteOrphansExcept(ids: List<Int>)

    @Query("DELETE FROM audiobooks")
    suspend fun deleteAll()

    @Query("UPDATE audiobooks SET isDownloaded = :downloaded WHERE id = :audiobookId")
    suspend fun setDownloaded(audiobookId: Int, downloaded: Boolean)

    @Query("SELECT * FROM audiobooks ORDER BY title")
    suspend fun getAllAudioBooksOnce(): List<AudioBookEntity>

    /** All downloaded audiobooks ordered alphabetically. Used for the Library tab in Android Auto. */
    @Query("SELECT * FROM audiobooks WHERE isDownloaded = 1 ORDER BY title ASC")
    fun getDownloadedAudioBooksAlphabetical(): Flow<List<AudioBookEntity>>

    /**
     * Standalone audiobooks with audio progress, ordered by most recently played.
     * Joins user_progress; updatedAt is a Long epoch-ms timestamp.
     */
    @Query("""
        SELECT ab.* FROM audiobooks ab
        INNER JOIN user_progress up ON up.mediaType = 'audiobook' AND up.mediaId = ab.id
        WHERE ab.isDownloaded = 1
          AND up.audioPositionMs IS NOT NULL
          AND up.audioPositionMs > 0
        ORDER BY up.updatedAt DESC
    """)
    fun getRecentlyPlayedStandaloneAudiobooks(): Flow<List<AudioBookEntity>>
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

    @Query("UPDATE bookmarks SET epubLocator = :locatorJson WHERE bookPairId = :pairId")
    suspend fun updateLocator(pairId: Int, locatorJson: String)

    @Query("UPDATE bookmarks SET epubLocator = :locatorJson, locatorAudioMs = :audioMs WHERE bookPairId = :pairId")
    suspend fun updateLocatorWithAudio(pairId: Int, locatorJson: String, audioMs: Int?)
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

@Dao
interface BookmarkLogDao {
    @Query("SELECT * FROM bookmark_log WHERE bookPairId = :pairId ORDER BY changedAt DESC LIMIT :limit")
    suspend fun getForPair(pairId: Int, limit: Int): List<BookmarkLogEntity>

    @Insert
    suspend fun insertLocal(entry: BookmarkLogEntity): Long

    @Query("SELECT localId FROM bookmark_log WHERE bookPairId = :pairId AND serverId = :serverId LIMIT 1")
    suspend fun findByServerId(pairId: Int, serverId: Int): Long?

    @Update
    suspend fun update(entry: BookmarkLogEntity)

    @Query("DELETE FROM bookmark_log WHERE bookPairId = :pairId AND serverId IS NULL AND changedAt <= :cutoff")
    suspend fun deleteLocalOnlyOlderThan(pairId: Int, cutoff: String)
}

@Dao
interface AcknowledgedItemDao {
    @Upsert
    suspend fun acknowledge(items: List<AcknowledgedItemEntity>)

    @Query("DELETE FROM acknowledged_items WHERE itemId IN (:ids) AND itemType = :type")
    suspend fun remove(ids: List<Int>, type: String)

    @Query("SELECT * FROM ebooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items WHERE itemType = 'ebook') ORDER BY uploadedAt DESC")
    fun getNewEbooks(): Flow<List<EBookEntity>>

    @Query("SELECT * FROM audiobooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items WHERE itemType = 'audiobook') ORDER BY uploadedAt DESC")
    fun getNewAudiobooks(): Flow<List<AudioBookEntity>>

    @Query("SELECT * FROM book_pairs WHERE id NOT IN (SELECT itemId FROM acknowledged_items WHERE itemType = 'pair') ORDER BY ebookTitle")
    fun getNewPairs(): Flow<List<BookPairEntity>>

    @Query("SELECT COUNT(*) FROM ebooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items WHERE itemType = 'ebook')")
    fun getNewEbookCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM audiobooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items WHERE itemType = 'audiobook')")
    fun getNewAudiobookCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM book_pairs WHERE id NOT IN (SELECT itemId FROM acknowledged_items WHERE itemType = 'pair')")
    fun getNewPairCount(): Flow<Int>
}
