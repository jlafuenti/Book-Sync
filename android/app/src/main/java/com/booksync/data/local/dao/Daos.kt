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

    /**
     * Record the cache state and the version it holds in one statement — they
     * must never disagree, or a stale map reads as current (issue #55).
     */
    @Query("UPDATE book_pairs SET syncMapDownloaded = :downloaded, syncMapVersion = :version WHERE id = :pairId")
    suspend fun setSyncMapCached(pairId: Int, downloaded: Boolean, version: Int?)

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
        INNER JOIN bookmarks b ON bp.id = b.bookPairId AND b.scopeKey = :scope
        WHERE bp.audiobookDownloaded = 1
          AND b.audioPositionMs > 0
        ORDER BY b.updatedAt DESC
    """)
    fun getRecentlyPlayedPairs(scope: String): Flow<List<BookPairEntity>>
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
            AND up.scopeKey = :scope
        WHERE eb.isDownloaded = 1
          AND up.isCompleted = 0
          AND (up.epubProgressPercent IS NOT NULL AND up.epubProgressPercent > 0
               OR up.epubChapter IS NOT NULL AND up.epubChapter > 0)
        ORDER BY up.updatedAt DESC
    """)
    fun getRecentlyReadEbooks(scope: String): Flow<List<EBookEntity>>
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
            AND up.scopeKey = :scope
        WHERE ab.isDownloaded = 1
          AND up.audioPositionMs IS NOT NULL
          AND up.audioPositionMs > 0
        ORDER BY up.updatedAt DESC
    """)
    fun getRecentlyPlayedStandaloneAudiobooks(scope: String): Flow<List<AudioBookEntity>>
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
    @Query("SELECT * FROM bookmarks WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun getBookmark(scope: String, pairId: Int): BookmarkEntity?

    @Query("SELECT * FROM bookmarks WHERE scopeKey = :scope AND bookPairId = :pairId")
    fun getBookmarkFlow(scope: String, pairId: Int): Flow<BookmarkEntity?>

    /**
     * Every bookmark for this account, for the library's "Recently opened" sort
     * (issue #223). A pair's last-opened moment lives here, not in
     * `user_progress` — see [com.booksync.data.repository.LastOpenedTimes].
     */
    @Query("SELECT * FROM bookmarks WHERE scopeKey = :scope")
    fun getAllBookmarksFlow(scope: String): Flow<List<BookmarkEntity>>

    @Upsert
    suspend fun upsertBookmark(bookmark: BookmarkEntity)

    @Query("UPDATE bookmarks SET syncedToServer = 0 WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun markUnsynced(scope: String, pairId: Int)

    // Companion to markUnsynced: saveReaderPosition writes the initial row
    // unsynced (so a crash between the Room write and the PUT doesn't leave a
    // row falsely claiming to be synced), then calls this once the canonical
    // PUT actually succeeds (issue #61/#40 fix 5).
    @Query("UPDATE bookmarks SET syncedToServer = 1 WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun markSynced(scope: String, pairId: Int)

    // Rows a throttled heartbeat (issue #65) or a failed PUT left behind —
    // processPendingSync pushes them on the WorkManager sweep, mirroring
    // UserProgressDao.getUnsyncedProgress.
    @Query("SELECT * FROM bookmarks WHERE scopeKey = :scope AND syncedToServer = 0")
    suspend fun getUnsyncedBookmarks(scope: String): List<BookmarkEntity>

    @Query("UPDATE bookmarks SET epubLocator = :locatorJson WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun updateLocator(scope: String, pairId: Int, locatorJson: String)

    @Query("UPDATE bookmarks SET epubLocator = :locatorJson, locatorAudioMs = :audioMs WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun updateLocatorWithAudio(scope: String, pairId: Int, locatorJson: String, audioMs: Int?)

    // A pair-level progress reset (issue: reset buttons not actually
    // resetting) must remove this row too — otherwise it resurrects the old
    // position on the next offline open and keeps routing resolvePairOpenTarget
    // to whatever format `source` still names.
    @Query("DELETE FROM bookmarks WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun deleteBookmark(scope: String, pairId: Int)
}

@Dao
interface PendingSyncDao {
    /**
     * The queue for one account (issue #314). Replaying another account's rows
     * would commit their reading position here, so the drain has no unscoped
     * read to reach for.
     */
    @Query("SELECT * FROM pending_sync WHERE scopeKey = :scope ORDER BY createdAt ASC")
    suspend fun getPendingForScope(scope: String): List<PendingSyncEntity>

    @Insert
    suspend fun insert(sync: PendingSyncEntity)

    @Delete
    suspend fun delete(sync: PendingSyncEntity)

    // See BookmarkDao.deleteBookmark — a queued retry carrying the pre-reset
    // position would otherwise replay it right back onto the server.
    @Query("DELETE FROM pending_sync WHERE scopeKey = :scope AND bookPairId = :pairId")
    suspend fun deleteForPair(scope: String, pairId: Int)
}

@Dao
interface UserProgressDao {
    @Query("SELECT * FROM user_progress WHERE scopeKey = :scope AND mediaType = :mediaType AND mediaId = :mediaId")
    suspend fun getProgress(scope: String, mediaType: String, mediaId: Int): UserProgressEntity?

    @Query("SELECT * FROM user_progress WHERE scopeKey = :scope AND mediaType = :mediaType AND mediaId = :mediaId")
    fun getProgressFlow(scope: String, mediaType: String, mediaId: Int): Flow<UserProgressEntity?>

    @Query("SELECT * FROM user_progress WHERE scopeKey = :scope")
    fun getAllProgressFlow(scope: String): Flow<List<UserProgressEntity>>

    @Upsert
    suspend fun upsertProgress(progress: UserProgressEntity)

    @Query("UPDATE user_progress SET syncedToServer = 0 WHERE scopeKey = :scope AND mediaType = :mediaType AND mediaId = :mediaId")
    suspend fun markUnsynced(scope: String, mediaType: String, mediaId: Int)

    // Companion to markUnsynced, mirroring BookmarkDao.markSynced:
    // savePlaybackPositionStandalone writes the row unsynced BEFORE the
    // canonical PUT (issue #164 — the write must survive a cancelled network
    // call), then calls this once the PUT actually succeeds.
    @Query("UPDATE user_progress SET syncedToServer = 1 WHERE scopeKey = :scope AND mediaType = :mediaType AND mediaId = :mediaId")
    suspend fun markSynced(scope: String, mediaType: String, mediaId: Int)

    // A pair-level progress reset (issue #61/#40 fix 3) must remove these rows
    // too — otherwise a stale, unsynced local row can be picked up by
    // syncAllBookmarksAndProgress/processPendingSync and pushed back to the
    // server, resurrecting the position the reset was supposed to have
    // cleared.
    @Query("DELETE FROM user_progress WHERE scopeKey = :scope AND mediaType = :mediaType AND mediaId = :mediaId")
    suspend fun deleteProgress(scope: String, mediaType: String, mediaId: Int)

    @Query("SELECT * FROM user_progress WHERE scopeKey = :scope AND syncedToServer = 0")
    suspend fun getUnsyncedProgress(scope: String): List<UserProgressEntity>
}

@Dao
interface BookmarkLogDao {
    @Query("SELECT * FROM bookmark_log WHERE scopeKey = :scope AND bookPairId = :pairId ORDER BY changedAt DESC LIMIT :limit")
    suspend fun getForPair(scope: String, pairId: Int, limit: Int): List<BookmarkLogEntity>

    @Insert
    suspend fun insertLocal(entry: BookmarkLogEntity): Long

    @Query("SELECT localId FROM bookmark_log WHERE scopeKey = :scope AND bookPairId = :pairId AND serverId = :serverId LIMIT 1")
    suspend fun findByServerId(scope: String, pairId: Int, serverId: Int): Long?

    @Update
    suspend fun update(entry: BookmarkLogEntity)

    @Query("DELETE FROM bookmark_log WHERE scopeKey = :scope AND bookPairId = :pairId AND serverId IS NULL AND changedAt <= :cutoff")
    suspend fun deleteLocalOnlyOlderThan(scope: String, pairId: Int, cutoff: String)
}

@Dao
interface AcknowledgedItemDao {
    @Upsert
    suspend fun acknowledge(items: List<AcknowledgedItemEntity>)

    @Query("DELETE FROM acknowledged_items WHERE scopeKey = :scope AND itemId IN (:ids) AND itemType = :type")
    suspend fun remove(scope: String, ids: List<Int>, type: String)

    @Query(
        "SELECT * FROM ebooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items " +
            "WHERE scopeKey = :scope AND itemType = 'ebook') ORDER BY uploadedAt DESC"
    )
    fun getNewEbooks(scope: String): Flow<List<EBookEntity>>

    @Query(
        "SELECT * FROM audiobooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items " +
            "WHERE scopeKey = :scope AND itemType = 'audiobook') ORDER BY uploadedAt DESC"
    )
    fun getNewAudiobooks(scope: String): Flow<List<AudioBookEntity>>

    @Query(
        "SELECT * FROM book_pairs WHERE id NOT IN (SELECT itemId FROM acknowledged_items " +
            "WHERE scopeKey = :scope AND itemType = 'pair') ORDER BY ebookTitle"
    )
    fun getNewPairs(scope: String): Flow<List<BookPairEntity>>

    @Query(
        "SELECT COUNT(*) FROM ebooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items " +
            "WHERE scopeKey = :scope AND itemType = 'ebook')"
    )
    fun getNewEbookCount(scope: String): Flow<Int>

    @Query(
        "SELECT COUNT(*) FROM audiobooks WHERE id NOT IN (SELECT itemId FROM acknowledged_items " +
            "WHERE scopeKey = :scope AND itemType = 'audiobook')"
    )
    fun getNewAudiobookCount(scope: String): Flow<Int>

    @Query(
        "SELECT COUNT(*) FROM book_pairs WHERE id NOT IN (SELECT itemId FROM acknowledged_items " +
            "WHERE scopeKey = :scope AND itemType = 'pair')"
    )
    fun getNewPairCount(scope: String): Flow<Int>
}
