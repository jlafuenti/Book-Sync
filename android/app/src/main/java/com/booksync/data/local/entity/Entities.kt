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
    val createdAt: Long = System.currentTimeMillis()
)
