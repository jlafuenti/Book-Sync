package com.booksync.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entities for offline storage.
 * These mirror the server models for local caching.
 */

@Entity(tableName = "book_pairs")
data class BookPairEntity(
    @PrimaryKey val id: Int,
    val ebookId: Int,
    val ebookTitle: String,
    val ebookAuthor: String?,
    val ebookFilename: String,
    val ebookFormat: String,
    val audiobookId: Int,
    val audiobookTitle: String,
    val audiobookAuthor: String?,
    val audiobookFilename: String,
    val audiobookFormat: String,
    val audiobookDurationSeconds: Int?,
    val status: String,
    val ebookDownloaded: Boolean = false,
    val audiobookDownloaded: Boolean = false,
    val syncMapDownloaded: Boolean = false,
    // Audiobook cover filename as served by /api/files/covers/{filename}.
    // Populated from AudioBookResponse.cover_path on library sync.
    val audiobookCoverPath: String? = null,
    val ebookSeries: String? = null,
    val ebookSeriesIndex: Float? = null,
)

@Entity(tableName = "ebooks")
data class EBookEntity(
    @PrimaryKey val id: Int,
    val title: String,
    val author: String?,
    val filename: String,
    val fileSize: Long?,
    val format: String,
    val series: String?,
    val seriesIndex: Float?,
    val uploadedAt: String,
    val isDownloaded: Boolean = false
)

@Entity(tableName = "audiobooks")
data class AudioBookEntity(
    @PrimaryKey val id: Int,
    val title: String,
    val author: String?,
    val filename: String,
    val durationSeconds: Int?,
    val format: String,
    val series: String?,
    val seriesIndex: Float?,
    val uploadedAt: String,
    val isDownloaded: Boolean = false,
    // Cover image filename as served by /api/files/covers/{filename}. Null until server provides it.
    val coverFilename: String? = null
)

@Entity(tableName = "sync_points")
data class SyncPointEntity(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val bookPairId: Int,
    val epubChapter: Int,
    val epubSentenceIndex: Int,
    val epubTextPreview: String?,
    val audioStartMs: Int,
    val audioEndMs: Int
)

@Entity(tableName = "bookmarks")
data class BookmarkEntity(
    @PrimaryKey val bookPairId: Int,
    val source: String,
    val epubChapter: Int?,
    val epubSentenceIndex: Int?,
    val audioPositionMs: Int?,
    val epubLocator: String? = null,  // Readium Locator JSON for precise EPUB position
    val updatedAt: String,
    val syncedToServer: Boolean = true
)

@Entity(tableName = "pending_sync")
data class PendingSyncEntity(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val bookPairId: Int,
    val source: String,
    val epubChapter: Int?,
    val epubSentenceIndex: Int?,
    val audioPositionMs: Int?,
    val epubLocator: String? = null,
    // Persisted so the SyncWorker's flushed request carries the same
    // history-log flag the original write intended. False for heartbeat
    // position saves, true for pause/stop/30-min-tick saves.
    val appendToLog: Boolean = false,
    val createdAt: Long = System.currentTimeMillis()
)

@Entity(tableName = "acknowledged_items", primaryKeys = ["itemId", "itemType"])
data class AcknowledgedItemEntity(
    val itemId: Int,
    val itemType: String  // "ebook", "audiobook", "pair"
)

@Entity(tableName = "user_progress", primaryKeys = ["mediaType", "mediaId"])
data class UserProgressEntity(
    val mediaType: String,
    val mediaId: Int,
    val bookPairId: Int?,
    val epubCfi: String?,
    val epubChapter: Int?,
    val epubProgressPercent: Float?,
    val audioPositionMs: Int?,
    val isCompleted: Boolean,
    val updatedAt: Long,
    val deviceId: String?,
    val syncedToServer: Boolean = true
)

/**
 * Local cache of bookmark change history.
 * Holds two kinds of rows:
 *   - Server-sourced (serverId != null), populated from GET /api/sync/bookmark/{pair}/log
 *   - Local-only (serverId == null), inserted when the user changes a bookmark offline
 * changedAt is normalized to ISO 8601 so lexicographic DESC sorts are correct across both origins.
 */
@Entity(tableName = "bookmark_log")
data class BookmarkLogEntity(
    @PrimaryKey(autoGenerate = true) val localId: Long = 0,
    val serverId: Int? = null,
    val bookPairId: Int,
    val source: String,
    val prevEpubChapter: Int?,
    val prevEpubSentenceIndex: Int?,
    val prevAudioPositionMs: Int?,
    val newEpubChapter: Int?,
    val newEpubSentenceIndex: Int?,
    val newAudioPositionMs: Int?,
    val changedAt: String
)
