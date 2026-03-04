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
    val syncMapDownloaded: Boolean = false
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
    val isDownloaded: Boolean = false
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
    val createdAt: Long = System.currentTimeMillis()
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
