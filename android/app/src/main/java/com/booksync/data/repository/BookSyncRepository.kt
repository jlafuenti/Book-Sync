package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.*
import com.booksync.data.local.entity.*
import com.booksync.data.remote.*
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Main repository that coordinates between the remote API and local database.
 * Implements offline-first pattern: writes go to local + sync queue,
 * reads prefer local cache.
 */
@Singleton
class BookSyncRepository @Inject constructor(
    private val api: BookSyncApi,
    private val bookPairDao: BookPairDao,
    private val syncPointDao: SyncPointDao,
    private val bookmarkDao: BookmarkDao,
    private val pendingSyncDao: PendingSyncDao,
    private val userProgressDao: UserProgressDao,
    @param:ApplicationContext private val context: Context,
) {
    // ============ Library ============

    /** Get all book pairs as a reactive Flow from local cache. */
    fun getPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getAllPairs()

    /** Refresh book pairs from the server and update local cache. */
    suspend fun refreshPairs() {
        val remotePairs = api.getPairs()
        val entities = remotePairs.map { pair ->
            val existing = bookPairDao.getPairById(pair.id)
            BookPairEntity(
                id = pair.id,
                ebookId = pair.ebook.id,
                ebookTitle = pair.ebook.title,
                ebookAuthor = pair.ebook.author,
                ebookFilename = pair.ebook.filename,
                ebookFormat = pair.ebook.format,
                audiobookId = pair.audiobook.id,
                audiobookTitle = pair.audiobook.title,
                audiobookAuthor = pair.audiobook.author,
                audiobookFilename = pair.audiobook.filename,
                audiobookFormat = pair.audiobook.format,
                audiobookDurationSeconds = pair.audiobook.duration_seconds,
                status = pair.status,
                ebookDownloaded = existing?.ebookDownloaded ?: false,
                audiobookDownloaded = existing?.audiobookDownloaded ?: false,
                syncMapDownloaded = existing?.syncMapDownloaded ?: false,
            )
        }
        bookPairDao.upsertPairs(entities)
    }
    /** Search the library remotely */
    suspend fun searchLibrary(query: String): SearchResponse {
        return api.searchLibrary(query)
    }

    // ============ Downloads ============

    /** Download the ebook file for a book pair. */
    suspend fun downloadEbook(pair: BookPairEntity): File {
        val response = api.downloadEbook(pair.ebookId)
        val dir = File(context.filesDir, "ebooks")
        dir.mkdirs()
        val file = File(dir, pair.ebookFilename)
        response.body()?.byteStream()?.use { input ->
            file.outputStream().use { output ->
                input.copyTo(output)
            }
        }
        bookPairDao.setEbookDownloaded(pair.id, true)
        return file
    }

    /** Download the audiobook file for a book pair. */
    suspend fun downloadAudiobook(pair: BookPairEntity): File {
        val response = api.downloadAudiobook(pair.audiobookId)
        val dir = File(context.filesDir, "audiobooks")
        dir.mkdirs()
        val file = File(dir, pair.audiobookFilename)
        response.body()?.byteStream()?.use { input ->
            file.outputStream().use { output ->
                input.copyTo(output)
            }
        }
        bookPairDao.setAudiobookDownloaded(pair.id, true)
        return file
    }

    /** Download the sync map for a book pair. */
    suspend fun downloadSyncMap(pairId: Int) {
        val syncMap = api.getSyncMap(pairId)

        // Clear old sync points
        syncPointDao.deletePointsForPair(pairId)

        // Save new sync points
        val entities = syncMap.sync_points.map { point ->
            SyncPointEntity(
                bookPairId = pairId,
                epubChapter = point.epub_chapter,
                epubSentenceIndex = point.epub_sentence_index,
                epubTextPreview = point.epub_text_preview,
                audioStartMs = point.audio_start_ms,
                audioEndMs = point.audio_end_ms,
            )
        }
        syncPointDao.insertPoints(entities)
        bookPairDao.setSyncMapDownloaded(pairId, true)
    }

    /** Get local file path for a downloaded ebook. */
    fun getEbookFile(pair: BookPairEntity): File =
        File(context.filesDir, "ebooks/${pair.ebookFilename}")

    /** Get local file path for a downloaded audiobook. */
    fun getAudiobookFile(pair: BookPairEntity): File =
        File(context.filesDir, "audiobooks/${pair.audiobookFilename}")

    // ============ Sync Points ============

    /** Get sync points from local cache. */
    suspend fun getSyncPoints(pairId: Int): List<SyncPointEntity> =
        syncPointDao.getPointsForPair(pairId)

    // ============ Bookmarks ============

    /** Get the current bookmark for a pair from local cache. */
    fun getBookmarkFlow(pairId: Int): Flow<BookmarkEntity?> =
        bookmarkDao.getBookmarkFlow(pairId)

    /** Refresh the bookmark from the server. */
    suspend fun refreshBookmark(pairId: Int) {
        try {
            val remote = api.getBookmark(pairId)
            bookmarkDao.upsertBookmark(
                BookmarkEntity(
                    bookPairId = pairId,
                    source = remote.source,
                    epubChapter = remote.epub_chapter,
                    epubSentenceIndex = remote.epub_sentence_index,
                    audioPositionMs = remote.audio_position_ms,
                    updatedAt = remote.updated_at,
                    syncedToServer = true,
                )
            )
        } catch (_: Exception) {
            // Offline — use local cache
        }
    }

    /**
     * Update bookmark position.
     * Saves locally immediately and queues sync to server.
     */
    suspend fun updateBookmark(
        pairId: Int,
        source: String,
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        audioPositionMs: Int? = null,
    ) {
        // Save locally
        bookmarkDao.upsertBookmark(
            BookmarkEntity(
                bookPairId = pairId,
                source = source,
                epubChapter = epubChapter,
                epubSentenceIndex = epubSentenceIndex,
                audioPositionMs = audioPositionMs,
                updatedAt = System.currentTimeMillis().toString(),
                syncedToServer = false,
            )
        )

        // Try immediate sync
        try {
            api.updateBookmark(
                pairId,
                BookmarkUpdateRequest(
                    source = source,
                    epub_chapter = epubChapter,
                    epub_sentence_index = epubSentenceIndex,
                    audio_position_ms = audioPositionMs,
                )
            )
            bookmarkDao.upsertBookmark(
                bookmarkDao.getBookmark(pairId)!!.copy(syncedToServer = true)
            )
        } catch (_: Exception) {
            // Offline — queue for later sync
            pendingSyncDao.insert(
                PendingSyncEntity(
                    bookPairId = pairId,
                    source = source,
                    epubChapter = epubChapter,
                    epubSentenceIndex = epubSentenceIndex,
                    audioPositionMs = audioPositionMs,
                )
            )
        }
    }

    // ============ Position Conversion ============

    /**
     * Convert an EPUB position to an audio position using the local sync map.
     * Returns the audio position in milliseconds with rewind applied.
     */
    suspend fun epubToAudio(
        pairId: Int,
        chapter: Int,
        sentenceIndex: Int,
        rewindMs: Int = 10_000,
    ): Int {
        val points = syncPointDao.getPointsForPair(pairId)

        // Find exact match
        val exact = points.find { it.epubChapter == chapter && it.epubSentenceIndex == sentenceIndex }
        if (exact != null) {
            return maxOf(0, exact.audioStartMs - rewindMs)
        }

        // Find closest preceding point
        val preceding = points.filter {
            it.epubChapter < chapter ||
                    (it.epubChapter == chapter && it.epubSentenceIndex <= sentenceIndex)
        }.lastOrNull()

        return if (preceding != null) {
            maxOf(0, preceding.audioStartMs - rewindMs)
        } else {
            0
        }
    }

    /**
     * Convert an audio position to an EPUB position using the local sync map.
     * Returns (chapter, sentenceIndex).
     */
    suspend fun audioToEpub(pairId: Int, audioPositionMs: Int): Pair<Int, Int> {
        val points = syncPointDao.getPointsForPair(pairId)

        val best = points.filter { it.audioStartMs <= audioPositionMs }.lastOrNull()
        return if (best != null) {
            Pair(best.epubChapter, best.epubSentenceIndex)
        } else {
            Pair(0, 0)
        }
    }

    // ============ User Progress ============

    /** Get the user progress flow from local cache */
    fun getProgressFlow(mediaType: String, mediaId: Int): Flow<UserProgressEntity?> =
        userProgressDao.getProgressFlow(mediaType, mediaId)

    /** Refresh progress from the server */
    suspend fun refreshProgress(mediaType: String, mediaId: Int) {
        try {
            val remote = api.getProgress(mediaType, mediaId)
            userProgressDao.upsertProgress(
                UserProgressEntity(
                    mediaType = remote.media_type,
                    mediaId = if (remote.media_type == "ebook") remote.ebook_id ?: 0 else remote.audiobook_id ?: 0,
                    bookPairId = remote.book_pair_id,
                    epubCfi = remote.epub_cfi,
                    epubChapter = remote.epub_chapter,
                    epubProgressPercent = remote.epub_progress_percent,
                    audioPositionMs = remote.audio_position_ms,
                    isCompleted = remote.is_completed,
                    updatedAt = System.currentTimeMillis(), // We ignore the remote string date for simpler local sorting
                    deviceId = remote.device_id,
                    syncedToServer = true
                )
            )
        } catch (_: Exception) {
            // Offline - use local cache
        }
    }

    /** Update progress locally and queue for sync */
    suspend fun updateProgress(
        mediaType: String,
        mediaId: Int,
        bookPairId: Int? = null,
        epubCfi: String? = null,
        epubChapter: Int? = null,
        epubProgressPercent: Float? = null,
        audioPositionMs: Int? = null,
        isCompleted: Boolean? = null,
        deviceId: String? = "android-device" // Ideally fetched from actual settings
    ) {
        val existing = userProgressDao.getProgress(mediaType, mediaId)
        
        // Merge with existing
        val merged = UserProgressEntity(
            mediaType = mediaType,
            mediaId = mediaId,
            bookPairId = bookPairId ?: existing?.bookPairId,
            epubCfi = epubCfi ?: existing?.epubCfi,
            epubChapter = epubChapter ?: existing?.epubChapter,
            epubProgressPercent = epubProgressPercent ?: existing?.epubProgressPercent,
            audioPositionMs = audioPositionMs ?: existing?.audioPositionMs,
            isCompleted = isCompleted ?: existing?.isCompleted ?: false,
            updatedAt = System.currentTimeMillis(),
            deviceId = deviceId,
            syncedToServer = false
        )
        
        userProgressDao.upsertProgress(merged)

        try {
            api.updateProgress(
                mediaType,
                mediaId,
                ProgressUpdateRequest(
                    book_pair_id = merged.bookPairId,
                    epub_cfi = merged.epubCfi,
                    epub_chapter = merged.epubChapter,
                    epub_progress_percent = merged.epubProgressPercent,
                    audio_position_ms = merged.audioPositionMs,
                    is_completed = merged.isCompleted,
                    device_id = merged.deviceId
                )
            )
            userProgressDao.upsertProgress(merged.copy(syncedToServer = true))
        } catch (_: Exception) {
            // Keep syncedToServer = false, will be picked up by SyncWorker
        }
    }

    // ============ Offline Sync ============

    /** Process pending sync queue — called by WorkManager. */
    suspend fun processPendingSync() {
        // ... (Process Bookmarks)
        val pendingBookmarks = pendingSyncDao.getAllPending()
        for (sync in pendingBookmarks) {
            try {
                api.updateBookmark(
                    sync.bookPairId,
                    BookmarkUpdateRequest(
                        source = sync.source,
                        epub_chapter = sync.epubChapter,
                        epub_sentence_index = sync.epubSentenceIndex,
                        audio_position_ms = sync.audioPositionMs,
                    )
                )
                pendingSyncDao.delete(sync)
            } catch (_: Exception) {
                break // Stop processing if still offline
            }
        }

        // Process Progress
        val unsyncedProgress = userProgressDao.getUnsyncedProgress()
        for (prog in unsyncedProgress) {
             try {
                 api.updateProgress(
                     prog.mediaType,
                     prog.mediaId,
                     ProgressUpdateRequest(
                         book_pair_id = prog.bookPairId,
                         epub_cfi = prog.epubCfi,
                         epub_chapter = prog.epubChapter,
                         epub_progress_percent = prog.epubProgressPercent,
                         audio_position_ms = prog.audioPositionMs,
                         is_completed = prog.isCompleted,
                         device_id = prog.deviceId
                     )
                 )
                 userProgressDao.upsertProgress(prog.copy(syncedToServer = true))
             } catch (_: Exception) {
                 break
             }
        }
    }
}
