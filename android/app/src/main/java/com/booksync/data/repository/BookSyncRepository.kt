package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.*
import com.booksync.data.local.entity.*
import com.booksync.data.remote.*
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.withContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Main repository that coordinates between the remote API and local database.
 * Implements offline-first pattern: writes go to local + sync queue,
 * reads prefer local cache.
 */
private const val REPO_TAG = "BookSyncRepository"

/** Where a tap on a paired book should land. */
enum class PairOpenTarget { Reader, Player, Details }

@Singleton
class BookSyncRepository @Inject constructor(
    private val api: BookSyncApi,
    private val bookPairDao: BookPairDao,
    private val eBookDao: EBookDao,
    private val audioBookDao: AudioBookDao,
    private val syncPointDao: SyncPointDao,
    private val bookmarkDao: BookmarkDao,
    private val pendingSyncDao: PendingSyncDao,
    private val userProgressDao: UserProgressDao,
    private val acknowledgedItemDao: AcknowledgedItemDao,
    private val bookmarkLogDao: BookmarkLogDao,
    @param:ApplicationContext private val context: Context,
    private val diagnosticLogger: DiagnosticLogger,
) {
    private fun log(msg: String) = diagnosticLogger.i(LogChannel.APP, REPO_TAG, msg)
    private fun logW(msg: String) = diagnosticLogger.w(LogChannel.APP, REPO_TAG, msg)
    private fun logE(msg: String, t: Throwable? = null) = diagnosticLogger.e(LogChannel.APP, REPO_TAG, msg, t)
    // ============ Library ============

    /** Get all book pairs as a reactive Flow from local cache. */
    fun getPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getAllPairs()

    /** Get downloaded book pairs as a reactive Flow from local cache. */
    fun getDownloadedPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getDownloadedPairs()

    /** Refresh book pairs from the server and update local cache. */
    suspend fun refreshPairs() {
        log("refreshPairs — fetching from server")
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
                audiobookCoverPath = pair.audiobook.cover_path ?: existing?.audiobookCoverPath,
                ebookSeries = pair.ebook.series,
                ebookSeriesIndex = pair.ebook.series_index,
            )
        }
        bookPairDao.upsertPairs(entities)
        val remoteIds = remotePairs.map { it.id }
        if (remoteIds.isEmpty()) bookPairDao.deleteAll() else bookPairDao.deleteOrphansExcept(remoteIds)
        log("refreshPairs — saved ${entities.size} pairs to cache")
    }
    
    /** Get all ebooks as a reactive Flow from local cache. */
    fun getEbooksFlow(): Flow<List<EBookEntity>> = eBookDao.getAllEBooks()

    /** Get downloaded ebooks as a reactive Flow from local cache. */
    fun getDownloadedEbooksFlow(): Flow<List<EBookEntity>> = eBookDao.getDownloadedEBooks()

    /** Refresh ebooks from the server and update local cache. */
    suspend fun refreshEbooks() {
        log("refreshEbooks — fetching from server")
        val remoteEbooks = api.getEbooks()
        val entities = remoteEbooks.map { ebook ->
            val existing = eBookDao.getEBookById(ebook.id)
            EBookEntity(
                id = ebook.id,
                title = ebook.title,
                author = ebook.author,
                filename = ebook.filename,
                fileSize = ebook.file_size,
                format = ebook.format,
                series = ebook.series,
                seriesIndex = ebook.series_index,
                uploadedAt = ebook.uploaded_at,
                isDownloaded = existing?.isDownloaded ?: false
            )
        }
        eBookDao.upsertEBooks(entities)
        val remoteIds = remoteEbooks.map { it.id }
        if (remoteIds.isEmpty()) eBookDao.deleteAll() else eBookDao.deleteOrphansExcept(remoteIds)
    }

    /** Get all audiobooks as a reactive Flow from local cache. */
    fun getAudiobooksFlow(): Flow<List<AudioBookEntity>> = audioBookDao.getAllAudioBooks()

    /** Get downloaded audiobooks as a reactive Flow from local cache. */
    fun getDownloadedAudiobooksFlow(): Flow<List<AudioBookEntity>> = audioBookDao.getDownloadedAudioBooks()

    /** Downloaded audiobooks ordered alphabetically. Used for the Android Auto Library tab. */
    fun getDownloadedAudiobooksAlphabeticalFlow(): Flow<List<AudioBookEntity>> =
        audioBookDao.getDownloadedAudioBooksAlphabetical()

    /** Pairs that have audio progress, ordered by most recently listened. Used for Android Auto Continue Listening. */
    fun getRecentlyPlayedPairsFlow(): Flow<List<BookPairEntity>> =
        combine(
            bookPairDao.getRecentlyPlayedPairs(),
            userProgressDao.getAllProgressFlow(),
        ) { pairs, allProgress ->
            val completedAudiobookIds = allProgress
                .filter { it.mediaType == "audiobook" && it.isCompleted }
                .map { it.mediaId }
                .toSet()
            pairs.filter { pair -> pair.audiobookId !in completedAudiobookIds }
        }

    /** Standalone audiobooks with audio progress, ordered by most recently played. */
    fun getRecentlyPlayedStandaloneAudiobooksFlow(): Flow<List<AudioBookEntity>> =
        combine(
            audioBookDao.getRecentlyPlayedStandaloneAudiobooks(),
            userProgressDao.getAllProgressFlow(),
        ) { audiobooks, allProgress ->
            val completedIds = allProgress
                .filter { it.mediaType == "audiobook" && it.isCompleted }
                .map { it.mediaId }
                .toSet()
            audiobooks.filter { it.id !in completedIds }
        }

    /** Ebooks with reading progress, ordered by most recently read. */
    fun getRecentlyReadEbooksFlow(): Flow<List<EBookEntity>> =
        combine(
            eBookDao.getRecentlyReadEbooks(),
            userProgressDao.getAllProgressFlow(),
        ) { ebooks, allProgress ->
            val completedIds = allProgress
                .filter { it.mediaType == "ebook" && it.isCompleted }
                .map { it.mediaId }
                .toSet()
            ebooks.filter { it.id !in completedIds }
        }

    /** Mark a media item as completed. */
    suspend fun markComplete(mediaType: String, mediaId: Int) {
        updateProgress(mediaType, mediaId, isCompleted = true)
    }

    /** Reset progress for a media item (sets to 0, not completed). */
    suspend fun resetMediaProgress(mediaType: String, mediaId: Int) {
        updateProgress(
            mediaType = mediaType,
            mediaId = mediaId,
            epubCfi = "",
            epubChapter = 0,
            epubProgressPercent = 0f,
            audioPositionMs = 0,
            isCompleted = false
        )
    }

    /** Get the current bookmark for a pair (single snapshot, not a flow). */
    suspend fun getBookmark(pairId: Int): com.booksync.data.local.entity.BookmarkEntity? =
        bookmarkDao.getBookmark(pairId)

    /** Get user progress for a given media item (single snapshot). */
    suspend fun getProgressOnce(mediaType: String, mediaId: Int): com.booksync.data.local.entity.UserProgressEntity? =
        userProgressDao.getProgress(mediaType, mediaId)

    /** Refresh audiobooks from the server and update local cache. */
    suspend fun refreshAudiobooks() {
        log("refreshAudiobooks — fetching from server")
        val remoteAudiobooks = api.getAudiobooks()
        val entities = remoteAudiobooks.map { audio ->
            val existing = audioBookDao.getAudioBookById(audio.id)
            AudioBookEntity(
                id = audio.id,
                title = audio.title,
                author = audio.author,
                filename = audio.filename,
                durationSeconds = audio.duration_seconds,
                format = audio.format,
                series = audio.series,
                seriesIndex = audio.series_index,
                uploadedAt = audio.uploaded_at,
                isDownloaded = existing?.isDownloaded ?: false,
                coverFilename = audio.cover_path ?: existing?.coverFilename
            )
        }
        audioBookDao.upsertAudioBooks(entities)
        val remoteIds = remoteAudiobooks.map { it.id }
        if (remoteIds.isEmpty()) audioBookDao.deleteAll() else audioBookDao.deleteOrphansExcept(remoteIds)
    }

    // ============ Pairing ============

    /** Create a new book pair on the server and refresh local cache. */
    suspend fun createPair(ebookId: Int, audiobookId: Int) {
        api.createPair(CreatePairRequest(ebook_id = ebookId, audiobook_id = audiobookId))
        refreshPairs()
    }

    /** Delete a book pair on the server and refresh local cache. */
    suspend fun deletePair(pairId: Int) {
        api.deletePair(pairId)
        bookPairDao.deletePairById(pairId)
    }

    /** Get unpaired ebooks (ebooks not in any pair). */
    suspend fun getUnpairedEbooks(): List<EBookEntity> {
        val allEbooks = eBookDao.getAllEBooksOnce()
        val pairedEbookIds = bookPairDao.getAllPairsOnce().map { it.ebookId }.toSet()
        return allEbooks.filter { it.id !in pairedEbookIds }
    }

    /** Get unpaired audiobooks (audiobooks not in any pair). */
    suspend fun getUnpairedAudiobooks(): List<AudioBookEntity> {
        val allAudiobooks = audioBookDao.getAllAudioBooksOnce()
        val pairedAudiobookIds = bookPairDao.getAllPairsOnce().map { it.audiobookId }.toSet()
        return allAudiobooks.filter { it.id !in pairedAudiobookIds }
    }

    /** Get a single pair by ID. */
    suspend fun getPairById(pairId: Int): BookPairEntity? = bookPairDao.getPairById(pairId)

    /**
     * Decide where a pair-tap should land:
     *  - If the user has a bookmark, honor `source` ("audiobook" → Player, "ebook" → Reader)
     *    provided that side is downloaded.
     *  - Otherwise prefer ebook → audiobook → details (matches LibraryScreen.openItem fallback).
     */
    suspend fun resolvePairOpenTarget(pair: BookPairEntity): PairOpenTarget {
        val source = bookmarkDao.getBookmark(pair.id)?.source
        return when {
            source == "audiobook" && pair.audiobookDownloaded -> PairOpenTarget.Player
            source == "ebook"     && pair.ebookDownloaded     -> PairOpenTarget.Reader
            pair.ebookDownloaded                              -> PairOpenTarget.Reader
            pair.audiobookDownloaded                          -> PairOpenTarget.Player
            else                                              -> PairOpenTarget.Details
        }
    }

    suspend fun resolvePairOpenTarget(pairId: Int): PairOpenTarget =
        getPairById(pairId)?.let { resolvePairOpenTarget(it) } ?: PairOpenTarget.Details

    /** Get a single ebook by ID. */
    suspend fun getEbookById(ebookId: Int): EBookEntity? = eBookDao.getEBookById(ebookId)

    /** Get a single audiobook by ID. */
    suspend fun getAudiobookById(audiobookId: Int): AudioBookEntity? = audioBookDao.getAudioBookById(audiobookId)

    /** Reactive single-pair flow for the details screen. */
    fun getPairByIdFlow(pairId: Int): Flow<BookPairEntity?> = bookPairDao.getPairByIdFlow(pairId)

    /** Reactive single-ebook flow for the details screen. */
    fun getEbookByIdFlow(ebookId: Int): Flow<EBookEntity?> = eBookDao.getEBookByIdFlow(ebookId)

    /** Reactive single-audiobook flow for the details screen. */
    fun getAudiobookByIdFlow(audiobookId: Int): Flow<AudioBookEntity?> = audioBookDao.getAudioBookByIdFlow(audiobookId)

    /** Search the library remotely */
    suspend fun searchLibrary(query: String): SearchResponse {
        return api.searchLibrary(query)
    }

    // ============ Downloads ============

    /** Download the ebook file for a book pair. */
    suspend fun downloadEbook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        log("downloadEbook — pairId=${pair.id} file=${pair.ebookFilename}")
        val response = api.downloadEbook(pair.ebookId)
        if (!response.isSuccessful) {
            logE("downloadEbook failed — HTTP ${response.code()}")
            throw Exception("HTTP ${response.code()}: ${response.errorBody()?.string()}")
        }
        val dir = File(context.filesDir, "ebooks")
        dir.mkdirs()
        val file = File(dir, pair.ebookFilename)
        withContext(Dispatchers.IO) {
            val body = response.body() ?: throw Exception("Empty response body")
            val contentLength = body.contentLength()
            body.byteStream().use { input ->
                file.outputStream().use { output ->
                    val buffer = ByteArray(8 * 1024)
                    var bytesCopied = 0L
                    var bytes = input.read(buffer)
                    while(bytes >= 0) {
                        output.write(buffer, 0, bytes)
                        bytesCopied += bytes
                        if (contentLength > 0) {
                            onProgress((bytesCopied * 100 / contentLength).toInt())
                        }
                        bytes = input.read(buffer)
                    }
                }
            }
        }
        bookPairDao.setEbookDownloaded(pair.id, true)
        log("downloadEbook complete — ${file.length() / 1024}KB")
        return file
    }

    /** Download a standalone ebook file. */
    suspend fun downloadStandaloneEbook(ebook: EBookEntity, onProgress: (Int) -> Unit = {}): File {
        val response = api.downloadEbook(ebook.id)
        if (!response.isSuccessful) {
            throw Exception("HTTP ${response.code()}: ${response.errorBody()?.string()}")
        }
        val dir = File(context.filesDir, "ebooks")
        dir.mkdirs()
        val file = File(dir, ebook.filename)
        withContext(Dispatchers.IO) {
            val body = response.body() ?: throw Exception("Empty response body")
            val contentLength = body.contentLength()
            body.byteStream().use { input ->
                file.outputStream().use { output ->
                    val buffer = ByteArray(8 * 1024)
                    var bytesCopied = 0L
                    var lastProgress = -1
                    var bytes = input.read(buffer)
                    while(bytes >= 0) {
                        output.write(buffer, 0, bytes)
                        bytesCopied += bytes
                        if (contentLength > 0) {
                            val progress = (bytesCopied * 100 / contentLength).toInt()
                            if (progress != lastProgress) {
                                lastProgress = progress
                                onProgress(progress)
                            }
                        }
                        bytes = input.read(buffer)
                    }
                }
            }
        }
        eBookDao.setDownloaded(ebook.id, true)
        return file
    }

    /** Download the audiobook file for a book pair. */
    suspend fun downloadAudiobook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        log("downloadAudiobook — pairId=${pair.id} file=${pair.audiobookFilename}")
        val response = api.downloadAudiobook(pair.audiobookId)
        if (!response.isSuccessful) {
            logE("downloadAudiobook failed — HTTP ${response.code()}")
            throw Exception("HTTP ${response.code()}: ${response.errorBody()?.string()}")
        }
        val dir = File(context.filesDir, "audiobooks")
        dir.mkdirs()
        val file = File(dir, pair.audiobookFilename)
        withContext(Dispatchers.IO) {
            val body = response.body() ?: throw Exception("Empty response body")
            val contentLength = body.contentLength()
            body.byteStream().use { input ->
                file.outputStream().use { output ->
                    val buffer = ByteArray(8 * 1024)
                    var bytesCopied = 0L
                    var lastProgress = -1
                    var bytes = input.read(buffer)
                    while(bytes >= 0) {
                        output.write(buffer, 0, bytes)
                        bytesCopied += bytes
                        if (contentLength > 0) {
                            val progress = (bytesCopied * 100 / contentLength).toInt()
                            if (progress != lastProgress) {
                                lastProgress = progress
                                onProgress(progress)
                            }
                        }
                        bytes = input.read(buffer)
                    }
                }
            }
        }
        bookPairDao.setAudiobookDownloaded(pair.id, true)
        log("downloadAudiobook complete — ${file.length() / 1024}KB")
        return file
    }

    /** Download a standalone audiobook file. */
    suspend fun downloadStandaloneAudiobook(audio: AudioBookEntity, onProgress: (Int) -> Unit = {}): File {
        val response = api.downloadAudiobook(audio.id)
        val dir = File(context.filesDir, "audiobooks")
        dir.mkdirs()
        val file = File(dir, audio.filename)
        withContext(Dispatchers.IO) {
            val body = response.body() ?: return@withContext
            val contentLength = body.contentLength()
            body.byteStream().use { input ->
                file.outputStream().use { output ->
                    val buffer = ByteArray(8 * 1024)
                    var bytesCopied = 0L
                    var lastProgress = -1
                    var bytes = input.read(buffer)
                    while(bytes >= 0) {
                        output.write(buffer, 0, bytes)
                        bytesCopied += bytes
                        if (contentLength > 0) {
                            val progress = (bytesCopied * 100 / contentLength).toInt()
                            if (progress != lastProgress) {
                                lastProgress = progress
                                onProgress(progress)
                            }
                        }
                        bytes = input.read(buffer)
                    }
                }
            }
        }
        audioBookDao.setDownloaded(audio.id, true)
        return file
    }

    /** Reset the syncMapDownloaded flag so a re-download is triggered. */
    suspend fun resetSyncMapDownloaded(pairId: Int) {
        bookPairDao.setSyncMapDownloaded(pairId, false)
    }

    /** Download the sync map for a book pair. */
    suspend fun downloadSyncMap(pairId: Int) {
        log("downloadSyncMap — pairId=$pairId")
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
                confidence = point.confidence,
            )
        }
        syncPointDao.insertPoints(entities)
        bookPairDao.setSyncMapDownloaded(pairId, true)
        log("downloadSyncMap complete — ${entities.size} sync points saved")
    }

    /**
     * Best-effort sync-map fetch with exponential backoff (1s/3s/10s).
     * Returns true when the sync map landed, false when the server has no sync map
     * yet (404) or every attempt failed. Never throws — callers treat a false
     * return as "try again later" and move on.
     */
    suspend fun downloadSyncMapWithRetry(pairId: Int): Boolean {
        val delaysMs = longArrayOf(1_000L, 3_000L, 10_000L)
        repeat(delaysMs.size) { attempt ->
            try {
                downloadSyncMap(pairId)
                return true
            } catch (e: retrofit2.HttpException) {
                if (e.code() == 404) {
                    log("downloadSyncMapWithRetry — 404 for pair $pairId; sync map not ready yet")
                    return false
                }
                log("downloadSyncMapWithRetry — HTTP ${e.code()} (attempt ${attempt + 1}/${delaysMs.size}): ${e.message()}")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log("downloadSyncMapWithRetry — error (attempt ${attempt + 1}/${delaysMs.size}): ${e.message}")
            }
            if (attempt < delaysMs.size - 1) kotlinx.coroutines.delay(delaysMs[attempt])
        }
        return false
    }

    fun getEbookFile(pair: BookPairEntity): File =
        File(context.filesDir, "ebooks/${pair.ebookFilename}")

    fun getAudiobookFile(pair: BookPairEntity): File =
        File(context.filesDir, "audiobooks/${pair.audiobookFilename}")

    suspend fun deleteEbook(pair: BookPairEntity) {
        getEbookFile(pair).delete()
        bookPairDao.setEbookDownloaded(pair.id, false)
    }

    suspend fun deleteAudiobook(pair: BookPairEntity) {
        getAudiobookFile(pair).delete()
        bookPairDao.setAudiobookDownloaded(pair.id, false)
    }

    suspend fun deleteStandaloneEbook(ebook: EBookEntity) {
        File(context.filesDir, "ebooks/${ebook.filename}").delete()
        eBookDao.setDownloaded(ebook.id, false)
    }

    suspend fun deleteStandaloneAudiobook(audio: AudioBookEntity) {
        File(context.filesDir, "audiobooks/${audio.filename}").delete()
        audioBookDao.setDownloaded(audio.id, false)
    }

    // ============ Sync Points ============

    /** Get sync points from local cache. */
    suspend fun getSyncPoints(pairId: Int): List<SyncPointEntity> =
        syncPointDao.getPointsForPair(pairId)

    // ============ Bookmarks ============

    /** Get the current bookmark for a pair from local cache. */
    fun getBookmarkFlow(pairId: Int): Flow<BookmarkEntity?> =
        bookmarkDao.getBookmarkFlow(pairId)

    /** Refresh the bookmark from the server, respecting unsynced local data. */
    suspend fun refreshBookmark(pairId: Int) {
        try {
            val remote = api.getBookmark(pairId)
            val existing = bookmarkDao.getBookmark(pairId)

            // Never overwrite unsynced local data — offline progress must survive
            if (existing != null && !existing.syncedToServer) {
                log("refreshBookmark pair=$pairId: local has unsynced changes, skipping server pull")
                return
            }

            // Only pull if server is newer or no local data exists
            val remoteTs = parseTimestamp(remote.updated_at)
            val localTs = parseTimestamp(existing?.updatedAt)

            if (existing == null || remoteTs >= localTs) {
                bookmarkDao.upsertBookmark(
                    BookmarkEntity(
                        bookPairId = pairId,
                        source = remote.source,
                        epubChapter = remote.epub_chapter,
                        epubSentenceIndex = remote.epub_sentence_index,
                        audioPositionMs = remote.audio_position_ms,
                        epubLocator = remote.epub_locator ?: existing?.epubLocator,
                        locatorAudioMs = existing?.locatorAudioMs,
                        updatedAt = remote.updated_at,
                        syncedToServer = true,
                    )
                )
            } else {
                log("refreshBookmark pair=$pairId: local is newer (local=$localTs, remote=$remoteTs), keeping local")
            }
        } catch (e: Exception) {
            logW("refreshBookmark offline — using local cache (${e.message})")
        }
    }

    /**
     * Fetch bookmark history.
     *
     * Offline-first: on server success the local cache is upserted and the newest server
     * timestamp prunes local-only rows it has superseded. On failure the call falls through
     * to the local cache so offline users still see their own recent bookmark changes
     * (written by [updateBookmark]).
     */
    suspend fun getBookmarkHistory(pairId: Int, limit: Int = 50): List<BookmarkLogResponse> {
        try {
            val serverEntries = api.getBookmarkLog(pairId, limit)
            for (entry in serverEntries) {
                val existingLocalId = bookmarkLogDao.findByServerId(pairId, entry.id)
                val row = entry.toEntity(pairId).copy(localId = existingLocalId ?: 0L)
                if (existingLocalId != null) bookmarkLogDao.update(row) else bookmarkLogDao.insertLocal(row)
            }
            serverEntries.maxByOrNull { it.changed_at }?.let { newest ->
                bookmarkLogDao.deleteLocalOnlyOlderThan(pairId, newest.changed_at)
            }
        } catch (e: Exception) {
            logW("getBookmarkHistory offline — using local cache (${e.message})")
        }
        return bookmarkLogDao.getForPair(pairId, limit).map { it.toResponse() }
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
        epubLocator: String? = null,
        // Audio position the epubLocator corresponds to; enables exact locator
        // reuse when returning from the player with little audio movement.
        locatorAudioMs: Int? = null,
        // When false (heartbeat saves every 5s), only the Bookmark row is updated
        // and no BookmarkLog row is written — server and local history stay clean.
        // Set true on pause / stop / 30-min-continuous-playback boundaries.
        appendToLog: Boolean = false,
    ) {
        val existing = bookmarkDao.getBookmark(pairId)

        // Merge with existing
        val merged = BookmarkEntity(
            bookPairId = pairId,
            source = source,
            epubChapter = epubChapter ?: existing?.epubChapter,
            epubSentenceIndex = epubSentenceIndex ?: existing?.epubSentenceIndex,
            audioPositionMs = audioPositionMs ?: existing?.audioPositionMs,
            epubLocator = epubLocator ?: existing?.epubLocator,
            locatorAudioMs = locatorAudioMs
                ?: (if (epubLocator != null) audioPositionMs ?: existing?.locatorAudioMs
                    else existing?.locatorAudioMs),
            updatedAt = System.currentTimeMillis().toString(),
            syncedToServer = false,
        )

        // Save locally
        bookmarkDao.upsertBookmark(merged)

        // Record a local history entry ONLY on meaningful session boundaries so the
        // offline history mirrors what the server will record. Heartbeat saves stay
        // out of the log on both sides.
        if (appendToLog) {
            bookmarkLogDao.insertLocal(
                BookmarkLogEntity(
                    serverId = null,
                    bookPairId = pairId,
                    source = source,
                    prevEpubChapter = existing?.epubChapter,
                    prevEpubSentenceIndex = existing?.epubSentenceIndex,
                    prevAudioPositionMs = existing?.audioPositionMs,
                    newEpubChapter = merged.epubChapter,
                    newEpubSentenceIndex = merged.epubSentenceIndex,
                    newAudioPositionMs = merged.audioPositionMs,
                    changedAt = java.time.Instant.ofEpochMilli(System.currentTimeMillis()).toString(),
                )
            )
        }

        // Try immediate sync
        try {
            api.updateBookmark(
                pairId,
                BookmarkUpdateRequest(
                    source = source,
                    epub_chapter = merged.epubChapter,
                    epub_sentence_index = merged.epubSentenceIndex,
                    audio_position_ms = merged.audioPositionMs,
                    epub_locator = merged.epubLocator,
                    append_to_log = appendToLog,
                )
            )
            bookmarkDao.upsertBookmark(merged.copy(syncedToServer = true))
        } catch (e: Exception) {
            logW("updateBookmark sync failed — queuing for later (${e.message})")
            pendingSyncDao.insert(
                PendingSyncEntity(
                    bookPairId = pairId,
                    source = source,
                    epubChapter = merged.epubChapter,
                    epubSentenceIndex = merged.epubSentenceIndex,
                    audioPositionMs = merged.audioPositionMs,
                    epubLocator = merged.epubLocator,
                    appendToLog = appendToLog,
                )
            )
        }
    }

    /**
     * Update just the EPUB locator JSON for a bookmark (used by Readium reader).
     * When [audioMs] is provided, it records the audio position this locator
     * corresponds to, enabling exact locator reuse on the next reader open.
     */
    suspend fun updateBookmarkLocator(pairId: Int, locatorJson: String, audioMs: Int? = null) {
        if (audioMs != null) {
            bookmarkDao.updateLocatorWithAudio(pairId, locatorJson, audioMs)
        } else {
            bookmarkDao.updateLocator(pairId, locatorJson)
        }
    }

    // ============ Position Conversion ============

    /** Normalize text for comparison: lowercase, convert ALL whitespace to spaces, strip punctuation */
    private fun normalizeForSearch(text: String): String {
        return text.lowercase()
            // Convert newlines and tabs to spaces FIRST (before stripping non-alphanumeric)
            .replace('\n', ' ')
            .replace('\r', ' ')
            .replace('\t', ' ')
            // Convert Unicode whitespace variants to regular spaces
            .replace('\u00A0', ' ')  // non-breaking space (very common in epubs)
            .replace('\u2002', ' ')  // en space
            .replace('\u2003', ' ')  // em space
            .replace('\u2009', ' ')  // thin space
            .replace('\u200B', ' ')  // zero-width space
            .replace('\u202F', ' ')  // narrow no-break space
            .replace(Regex("[^a-z0-9 ]"), "") // Keep ONLY a-z, digits, regular space
            .replace(Regex(" +"), " ")        // Collapse multiple spaces
            .trim()
    }

    /** Character-bigram set of a normalized string, encoded as Ints for speed. */
    private fun bigramSet(s: String): HashSet<Int> {
        val set = HashSet<Int>(maxOf(16, s.length))
        for (i in 0 until s.length - 1) set.add(s[i].code * 1024 + s[i + 1].code)
        return set
    }

    /** Dice coefficient between two bigram sets: 2*|A∩B| / (|A|+|B|), 0..1. */
    private fun diceSimilarity(a: HashSet<Int>, b: HashSet<Int>): Double {
        if (a.isEmpty() || b.isEmpty()) return 0.0
        val (small, large) = if (a.size <= b.size) a to b else b to a
        var inter = 0
        for (x in small) if (x in large) inter++
        return 2.0 * inter / (a.size + b.size)
    }

    /**
     * Sliding-window fuzzy search: find the offset in [transcript] whose window
     * best matches [needle] by bigram Dice similarity. Returns (charOffset, score)
     * or null if below [threshold]. Tolerates transcription wording differences
     * that defeat exact substring search (mishears, "Mr." vs "mister", etc).
     */
    private fun fuzzyFindInTranscript(
        transcript: String,
        needle: String,
        threshold: Double = 0.60,
    ): Pair<Int, Double>? {
        if (needle.length < 20 || transcript.length < needle.length) return null
        val needleBigrams = bigramSet(needle)
        val window = needle.length
        val step = maxOf(10, window / 4)
        var bestOffset = -1
        var bestScore = 0.0
        var offset = 0
        while (offset + window <= transcript.length) {
            val score = diceSimilarity(needleBigrams, bigramSet(transcript.substring(offset, offset + window)))
            if (score > bestScore) { bestScore = score; bestOffset = offset }
            offset += step
        }
        // Refine around the best coarse hit with step 1 for a tighter offset
        if (bestOffset >= 0 && bestScore >= threshold) {
            var refinedOffset = bestOffset
            var refinedScore = bestScore
            val lo = maxOf(0, bestOffset - step)
            val hi = minOf(transcript.length - window, bestOffset + step)
            for (o in lo..hi) {
                val s = diceSimilarity(needleBigrams, bigramSet(transcript.substring(o, o + window)))
                if (s > refinedScore) { refinedScore = s; refinedOffset = o }
            }
            return refinedOffset to refinedScore
        }
        return null
    }

    /**
     * If [matchedPoint] is an interpolated point (confidence == 0), prefer the
     * nearest real whisper-matched point within ±3 list positions — its
     * timestamp came from the transcript, not interpolation.
     */
    private fun nudgeToConfidentPoint(points: List<SyncPointEntity>, matchedPointIdx: Int): SyncPointEntity {
        val matchedPoint = points[matchedPointIdx]
        if (matchedPoint.confidence > 0f) return matchedPoint
        val nearby = (maxOf(0, matchedPointIdx - 3)..minOf(points.lastIndex, matchedPointIdx + 3))
            .map { points[it] }
            .filter { it.confidence > 0.5f }
            .minByOrNull { kotlin.math.abs(it.epubSentenceIndex - matchedPoint.epubSentenceIndex) }
        return nearby ?: matchedPoint
    }

    /**
     * Find the best matching SyncPointEntity for a given extracted EPUB text snippet.
     *
     * NEW ALGORITHM: Instead of comparing against individual short sentence previews,
     * this concatenates ALL sync point previews for the chapter into one big normalized
     * text block, then does substring search to find where the ebook page text appears.
     * The character offset is mapped back to the corresponding sentence/sync point.
     */
    suspend fun getSyncPointForEpubText(pairId: Int, chapter: Int, epubText: String): SyncPointEntity? = withContext(Dispatchers.Default) {
        // Search the target chapter and neighboring chapters
        val allPoints = syncPointDao.getPointsForPair(pairId)
        
        if (allPoints.isEmpty()) {
            android.util.Log.d("SyncMatch", "No sync points found for pair $pairId")
            return@withContext null
        }

        // Log available chapters in sync map for debugging
        val availableChapters = allPoints.map { it.epubChapter }.distinct().sorted()
        android.util.Log.d("SyncMatch", "Available sync chapters: $availableChapters")

        val normalizedEpub = normalizeForSearch(epubText)
        android.util.Log.d("SyncMatch", "Searching transcript for epubText (length ${normalizedEpub.length}): '${normalizedEpub.take(100)}...'")

        // Try each chapter in range: target first, then expanding outward (±10 to handle offset issues)
        val chaptersToTry = listOf(chapter) + (1..10).flatMap { d -> listOf(chapter - d, chapter + d) }

        for (targetChapter in chaptersToTry) {
            val points = allPoints.filter { it.epubChapter == targetChapter }
                .sortedBy { it.epubSentenceIndex }
            if (points.isEmpty()) continue

            // Build concatenated transcript with sentence boundary tracking
            val transcriptBuilder = StringBuilder()
            val sentenceBoundaries = mutableListOf<Pair<Int, Int>>() // (startCharIndex, pointIndex)

            for ((idx, point) in points.withIndex()) {
                val preview = point.epubTextPreview ?: continue
                val normalized = normalizeForSearch(preview)
                if (normalized.isEmpty()) continue

                val startPos = transcriptBuilder.length
                transcriptBuilder.append(normalized)
                transcriptBuilder.append(" ") // Space between sentences
                sentenceBoundaries.add(Pair(startPos, idx))
            }

            val transcript = transcriptBuilder.toString()
            if (transcript.isEmpty()) continue

            // Log first 80 chars of transcript for debugging
            android.util.Log.d("SyncMatch", "Chapter $targetChapter transcript (${transcript.length} chars): '${transcript.take(80)}...'")

            // Progressive substring search: try decreasing lengths
            val searchLengths = listOf(
                minOf(normalizedEpub.length, 200),
                minOf(normalizedEpub.length, 150),
                minOf(normalizedEpub.length, 100),
                minOf(normalizedEpub.length, 60),
                minOf(normalizedEpub.length, 30),
            ).distinct().filter { it > 10 }

            for (searchLen in searchLengths) {
                // Try from the start of the extracted text
                val searchText = normalizedEpub.take(searchLen)
                var matchIndex = transcript.indexOf(searchText)

                // Also try from a bit into the text (skip potential chapter headings at start)
                if (matchIndex < 0 && normalizedEpub.length > searchLen + 30) {
                    val offsetText = normalizedEpub.substring(30).take(searchLen)
                    matchIndex = transcript.indexOf(offsetText)
                }

                if (matchIndex >= 0) {
                    // Map character offset to sentence index
                    var matchedPointIdx = 0
                    for ((startPos, idx) in sentenceBoundaries) {
                        if (startPos <= matchIndex) {
                            matchedPointIdx = idx
                        } else {
                            break
                        }
                    }

                    val matchedPoint = nudgeToConfidentPoint(points, matchedPointIdx)
                    android.util.Log.d("SyncMatch", "MATCH found in chapter $targetChapter! " +
                        "Sentence ${matchedPoint.epubSentenceIndex}, audio=${matchedPoint.audioStartMs}ms, " +
                        "searchLen=$searchLen, preview='${matchedPoint.epubTextPreview?.take(60)}'")
                    return@withContext matchedPoint
                }
            }

            // Exact substring failed for this chapter — try fuzzy bigram match,
            // which tolerates transcription wording differences.
            val fuzzy = fuzzyFindInTranscript(transcript, normalizedEpub.take(150))
            if (fuzzy != null) {
                val (matchIndex, score) = fuzzy
                var matchedPointIdx = 0
                for ((startPos, idx) in sentenceBoundaries) {
                    if (startPos <= matchIndex) matchedPointIdx = idx else break
                }
                val matchedPoint = nudgeToConfidentPoint(points, matchedPointIdx)
                android.util.Log.d("SyncMatch", "FUZZY match in chapter $targetChapter! " +
                    "score=%.2f sentence=${matchedPoint.epubSentenceIndex} audio=${matchedPoint.audioStartMs}ms".format(score))
                return@withContext matchedPoint
            }

            android.util.Log.d("SyncMatch", "No substring or fuzzy match in chapter $targetChapter")
        }

        android.util.Log.d("SyncMatch", "No match found in any chapter (searched ±10 around chapter $chapter)")
        return@withContext null
    }

    suspend fun getSentenceIndexFromProgression(pairId: Int, chapter: Int, progression: Float): Int {
        val points = syncPointDao.getPointsForPair(pairId).filter { it.epubChapter == chapter }
        if (points.isEmpty()) return 0
        val index = (progression * points.size).toInt().coerceIn(0, points.size - 1)
        return points[index].epubSentenceIndex
    }

    /**
     * Convert an EPUB position (chapter + extracted text) to an audio position using the local sync map.
     * Returns the audio position in milliseconds with rewind applied.
     */
    suspend fun epubToAudioText(
        pairId: Int,
        chapter: Int,
        epubText: String,
        rewindMs: Int = 10_000,
    ): Int {
        val syncPoint = getSyncPointForEpubText(pairId, chapter, epubText) ?: return 0
        return maxOf(0, syncPoint.audioStartMs - rewindMs)
    }

    /**
     * Convert an audio position to an EPUB text snippet using the local sync map.
     * Returns (chapter, epubTextPreview).
     */
    suspend fun audioToEpubText(pairId: Int, audioPositionMs: Int): Pair<Int, String> {
        val points = syncPointDao.getPointsForPair(pairId)

        // Sort by audioStartMs to find the correct sync point closest to the audio position
        val sorted = points.sortedBy { it.audioStartMs }
        val best = sorted.filter { it.audioStartMs <= audioPositionMs }.lastOrNull()
        android.util.Log.d("AudioToEpub", "audioToEpubText: audioPos=${audioPositionMs}ms, " +
            "best=${best?.let { "ch${it.epubChapter} s${it.epubSentenceIndex} audio=${it.audioStartMs}ms preview='${it.epubTextPreview?.take(50)}'" } ?: "null"}")
        return if (best != null) {
            Pair(best.epubChapter, best.epubTextPreview ?: "")
        } else {
            Pair(0, "")
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

    // ============ Timestamp Helpers ============

    /**
     * Parse a timestamp string that may be either epoch millis (local format)
     * or ISO 8601 datetime (server format) into epoch millis.
     */
    private fun parseTimestamp(ts: String?): Long {
        if (ts.isNullOrBlank()) return 0L
        // Try epoch millis first (local format, e.g. "1712880000000")
        ts.toLongOrNull()?.let { return it }
        // Try ISO 8601 with zone (e.g. "2026-04-12T15:30:00Z")
        return try {
            java.time.Instant.parse(ts).toEpochMilli()
        } catch (_: Exception) {
            // Try ISO 8601 without zone (e.g. "2026-04-12T15:30:00") — assume UTC
            try {
                java.time.LocalDateTime.parse(ts)
                    .atZone(java.time.ZoneOffset.UTC)
                    .toInstant()
                    .toEpochMilli()
            } catch (_: Exception) { 0L }
        }
    }

    // ============ Startup Bidirectional Sync ============

    /**
     * Bidirectional sync of bookmarks and audiobook progress for all pairs.
     * Called on startup after pairs are loaded to recover data lost by DB wipe or
     * to reconcile progress made on another device.
     *
     * - Server newer (or no local): pull from server
     * - Local newer: push local to server
     * - Equal / server unreachable: no-op
     */
    suspend fun syncAllBookmarksAndProgress(pairs: List<BookPairEntity>) {
        for (pair in pairs) {
            // --- Bookmark ---
            try {
                val remote = api.getBookmark(pair.id)
                val local = bookmarkDao.getBookmark(pair.id)
                val remoteTs = parseTimestamp(remote.updated_at)
                val localTs  = parseTimestamp(local?.updatedAt)

                when {
                    // Unsynced local data always wins — push to server
                    local != null && !local.syncedToServer -> {
                        api.updateBookmark(pair.id, BookmarkUpdateRequest(
                            source              = local.source,
                            epub_chapter        = local.epubChapter,
                            epub_sentence_index = local.epubSentenceIndex,
                            audio_position_ms   = local.audioPositionMs,
                            epub_locator        = local.epubLocator,
                        ))
                        bookmarkDao.upsertBookmark(local.copy(syncedToServer = true))
                        log("syncBookmark pair=${pair.id}: pushed unsynced local")
                    }
                    local == null || remoteTs > localTs -> {
                        bookmarkDao.upsertBookmark(BookmarkEntity(
                            bookPairId          = pair.id,
                            source              = remote.source,
                            epubChapter         = remote.epub_chapter,
                            epubSentenceIndex   = remote.epub_sentence_index,
                            audioPositionMs     = remote.audio_position_ms,
                            epubLocator         = remote.epub_locator ?: local?.epubLocator,
                            updatedAt           = remote.updated_at,
                            syncedToServer      = true,
                        ))
                        log("syncBookmark pair=${pair.id}: pulled from server ts=$remoteTs")
                    }
                    localTs > remoteTs -> {
                        api.updateBookmark(pair.id, BookmarkUpdateRequest(
                            source              = local.source,
                            epub_chapter        = local.epubChapter,
                            epub_sentence_index = local.epubSentenceIndex,
                            audio_position_ms   = local.audioPositionMs,
                            epub_locator        = local.epubLocator,
                        ))
                        bookmarkDao.upsertBookmark(local.copy(syncedToServer = true))
                        log("syncBookmark pair=${pair.id}: pushed local ts=$localTs")
                    }
                }
            } catch (_: Exception) { /* offline or no server record yet — skip */ }

            // --- Audiobook progress ---
            try {
                val remote = api.getProgress("audiobook", pair.audiobookId)
                val local  = userProgressDao.getProgress("audiobook", pair.audiobookId)
                val remoteTs = parseTimestamp(remote.updated_at)
                val localTs  = local?.updatedAt ?: 0L

                when {
                    // Unsynced local data always wins — push to server
                    local != null && !local.syncedToServer -> {
                        api.updateProgress("audiobook", pair.audiobookId, ProgressUpdateRequest(
                            book_pair_id        = local.bookPairId,
                            audio_position_ms   = local.audioPositionMs,
                            is_completed        = local.isCompleted,
                            device_id           = local.deviceId,
                        ))
                        userProgressDao.upsertProgress(local.copy(syncedToServer = true))
                        log("syncProgress audiobook=${pair.audiobookId}: pushed unsynced local")
                    }
                    local == null || remoteTs > localTs -> {
                        userProgressDao.upsertProgress(UserProgressEntity(
                            mediaType            = "audiobook",
                            mediaId              = pair.audiobookId,
                            bookPairId           = pair.id,
                            epubCfi              = null,
                            epubChapter          = null,
                            epubProgressPercent  = null,
                            audioPositionMs      = remote.audio_position_ms,
                            isCompleted          = remote.is_completed,
                            updatedAt            = remoteTs,
                            deviceId             = remote.device_id,
                            syncedToServer       = true,
                        ))
                        log("syncProgress audiobook=${pair.audiobookId}: pulled from server ts=$remoteTs")
                    }
                    localTs > remoteTs -> {
                        api.updateProgress("audiobook", pair.audiobookId, ProgressUpdateRequest(
                            book_pair_id        = local.bookPairId,
                            audio_position_ms   = local.audioPositionMs,
                            is_completed        = local.isCompleted,
                            device_id           = local.deviceId,
                        ))
                        userProgressDao.upsertProgress(local.copy(syncedToServer = true))
                        log("syncProgress audiobook=${pair.audiobookId}: pushed local ts=$localTs")
                    }
                }
            } catch (_: Exception) { /* offline or no server record yet — skip */ }
        }
    }

    // ============ Offline Sync ============

    /** Process pending sync queue — called by WorkManager. */
    suspend fun processPendingSync() {
        val pendingBookmarks = pendingSyncDao.getAllPending()
        if (pendingBookmarks.isNotEmpty()) log("processPendingSync — ${pendingBookmarks.size} pending bookmarks")
        for (sync in pendingBookmarks) {
            try {
                api.updateBookmark(
                    sync.bookPairId,
                    BookmarkUpdateRequest(
                        source = sync.source,
                        epub_chapter = sync.epubChapter,
                        epub_sentence_index = sync.epubSentenceIndex,
                        audio_position_ms = sync.audioPositionMs,
                        epub_locator = sync.epubLocator,
                        append_to_log = sync.appendToLog,
                    )
                )
                pendingSyncDao.delete(sync)
                log("processPendingSync — bookmark pairId=${sync.bookPairId} synced")
            } catch (e: Exception) {
                logW("processPendingSync — still offline, stopping (${e.message})")
                break
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

    // ============ New Items (unacknowledged) ============

    fun getNewEbooksFlow(): Flow<List<EBookEntity>> = acknowledgedItemDao.getNewEbooks()
    fun getNewAudiobooksFlow(): Flow<List<AudioBookEntity>> = acknowledgedItemDao.getNewAudiobooks()
    fun getNewPairsFlow(): Flow<List<BookPairEntity>> = acknowledgedItemDao.getNewPairs()
    fun getNewEbookCountFlow(): Flow<Int> = acknowledgedItemDao.getNewEbookCount()
    fun getNewAudiobookCountFlow(): Flow<Int> = acknowledgedItemDao.getNewAudiobookCount()
    fun getNewPairCountFlow(): Flow<Int> = acknowledgedItemDao.getNewPairCount()

    suspend fun acknowledgeItems(ids: List<Int>, type: String) {
        acknowledgedItemDao.acknowledge(ids.map { AcknowledgedItemEntity(it, type) })
    }
}

// ============ BookmarkLog mappers ============

private fun BookmarkLogResponse.toEntity(pairId: Int) = BookmarkLogEntity(
    serverId = id,
    bookPairId = pairId,
    source = source,
    prevEpubChapter = prev_epub_chapter,
    prevEpubSentenceIndex = prev_epub_sentence_index,
    prevAudioPositionMs = prev_audio_position_ms,
    newEpubChapter = new_epub_chapter,
    newEpubSentenceIndex = new_epub_sentence_index,
    newAudioPositionMs = new_audio_position_ms,
    changedAt = changed_at,
)

private fun BookmarkLogEntity.toResponse() = BookmarkLogResponse(
    id = serverId ?: -localId.toInt(),
    source = source,
    prev_epub_chapter = prevEpubChapter,
    prev_epub_sentence_index = prevEpubSentenceIndex,
    prev_audio_position_ms = prevAudioPositionMs,
    new_epub_chapter = newEpubChapter,
    new_epub_sentence_index = newEpubSentenceIndex,
    new_audio_position_ms = newAudioPositionMs,
    changed_at = changedAt,
)
