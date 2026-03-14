package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.*
import com.booksync.data.local.entity.*
import com.booksync.data.remote.*
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext
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
    private val eBookDao: EBookDao,
    private val audioBookDao: AudioBookDao,
    private val syncPointDao: SyncPointDao,
    private val bookmarkDao: BookmarkDao,
    private val pendingSyncDao: PendingSyncDao,
    private val userProgressDao: UserProgressDao,
    @param:ApplicationContext private val context: Context,
) {
    // ============ Library ============

    /** Get all book pairs as a reactive Flow from local cache. */
    fun getPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getAllPairs()

    /** Get downloaded book pairs as a reactive Flow from local cache. */
    fun getDownloadedPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getDownloadedPairs()

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
    
    /** Get all ebooks as a reactive Flow from local cache. */
    fun getEbooksFlow(): Flow<List<EBookEntity>> = eBookDao.getAllEBooks()

    /** Get downloaded ebooks as a reactive Flow from local cache. */
    fun getDownloadedEbooksFlow(): Flow<List<EBookEntity>> = eBookDao.getDownloadedEBooks()

    /** Refresh ebooks from the server and update local cache. */
    suspend fun refreshEbooks() {
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
        bookPairDao.getRecentlyPlayedPairs()

    /** Standalone audiobooks with audio progress, ordered by most recently played. */
    fun getRecentlyPlayedStandaloneAudiobooksFlow(): Flow<List<AudioBookEntity>> =
        audioBookDao.getRecentlyPlayedStandaloneAudiobooks()

    /** Get the current bookmark for a pair (single snapshot, not a flow). */
    suspend fun getBookmark(pairId: Int): com.booksync.data.local.entity.BookmarkEntity? =
        bookmarkDao.getBookmark(pairId)

    /** Get user progress for a given media item (single snapshot). */
    suspend fun getProgressOnce(mediaType: String, mediaId: Int): com.booksync.data.local.entity.UserProgressEntity? =
        userProgressDao.getProgress(mediaType, mediaId)

    /** Refresh audiobooks from the server and update local cache. */
    suspend fun refreshAudiobooks() {
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

    /** Get a single ebook by ID. */
    suspend fun getEbookById(ebookId: Int): EBookEntity? = eBookDao.getEBookById(ebookId)

    /** Get a single audiobook by ID. */
    suspend fun getAudiobookById(audiobookId: Int): AudioBookEntity? = audioBookDao.getAudioBookById(audiobookId)

    /** Search the library remotely */
    suspend fun searchLibrary(query: String): SearchResponse {
        return api.searchLibrary(query)
    }

    // ============ Downloads ============

    /** Download the ebook file for a book pair. */
    suspend fun downloadEbook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        val response = api.downloadEbook(pair.ebookId)
        if (!response.isSuccessful) {
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
        val response = api.downloadAudiobook(pair.audiobookId)
        if (!response.isSuccessful) {
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

    /** Refresh the bookmark from the server. */
    suspend fun refreshBookmark(pairId: Int) {
        try {
            val remote = api.getBookmark(pairId)
            val existing = bookmarkDao.getBookmark(pairId)
            bookmarkDao.upsertBookmark(
                BookmarkEntity(
                    bookPairId = pairId,
                    source = remote.source,
                    epubChapter = remote.epub_chapter,
                    epubSentenceIndex = remote.epub_sentence_index,
                    audioPositionMs = remote.audio_position_ms,
                    epubLocator = remote.epub_locator ?: existing?.epubLocator,
                    updatedAt = remote.updated_at,
                    syncedToServer = true,
                )
            )
        } catch (_: Exception) {
            // Offline — use local cache
        }
    }

    /**
     * Fetch bookmark history from the server.
     */
    suspend fun getBookmarkHistory(pairId: Int, limit: Int = 50): List<BookmarkLogResponse> {
        return try {
            api.getBookmarkLog(pairId, limit)
        } catch (e: Exception) {
            emptyList()
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
        epubLocator: String? = null,
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
            updatedAt = System.currentTimeMillis().toString(),
            syncedToServer = false,
        )

        // Save locally
        bookmarkDao.upsertBookmark(merged)

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
                )
            )
            bookmarkDao.upsertBookmark(merged.copy(syncedToServer = true))
        } catch (_: Exception) {
            // Offline — queue for later sync
            pendingSyncDao.insert(
                PendingSyncEntity(
                    bookPairId = pairId,
                    source = source,
                    epubChapter = merged.epubChapter,
                    epubSentenceIndex = merged.epubSentenceIndex,
                    audioPositionMs = merged.audioPositionMs,
                    epubLocator = merged.epubLocator,
                )
            )
        }
    }

    /** Update just the EPUB locator JSON for a bookmark (used by Readium reader). */
    suspend fun updateBookmarkLocator(pairId: Int, locatorJson: String) {
        bookmarkDao.updateLocator(pairId, locatorJson)
    }

    // ============ Position Conversion ============

    /** Normalize text for comparison: lowercase, convert ALL whitespace to spaces, strip punctuation */
    private fun normalizeForSearch(text: String): String {
        return text.lowercase()
            // Convert ALL Unicode whitespace variants to regular spaces FIRST
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

                    val matchedPoint = points[matchedPointIdx]
                    android.util.Log.d("SyncMatch", "MATCH found in chapter $targetChapter! " +
                        "Sentence ${matchedPoint.epubSentenceIndex}, audio=${matchedPoint.audioStartMs}ms, " +
                        "searchLen=$searchLen, preview='${matchedPoint.epubTextPreview?.take(60)}'")
                    return@withContext matchedPoint
                }
            }

            android.util.Log.d("SyncMatch", "No substring match in chapter $targetChapter")
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
                        epub_locator = sync.epubLocator,
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
