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
    @ApplicationContext private val context: Context,
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

    // ============ Offline Sync ============

    /** Process pending sync queue — called by WorkManager. */
    suspend fun processPendingSync() {
        val pending = pendingSyncDao.getAllPending()
        for (sync in pending) {
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
    }
}
