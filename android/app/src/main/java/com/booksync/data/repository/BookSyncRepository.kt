package com.booksync.data.repository

import com.booksync.data.local.entity.*
import com.booksync.data.remote.*
import com.booksync.player.PlaybackOffsets
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Facade over the Android data layer, kept so existing callers do not change
 * (issue #224). It owns no logic of its own any more: every method is a
 * one-line delegation, with the same defaults, to the seam that does —
 *
 *  - [LibraryRepository] — the catalogue mirror, refreshes, pairing, lookup,
 *    the Continue / Recently-opened flows, search, NEW-item acknowledgements;
 *  - [MediaDownloadRepository] — files on disk and the cached sync map;
 *  - [PositionRepository] — everything under docs/position-sync-contract.md.
 *
 * New code should inject the seam it needs directly; a facade method can be
 * deleted once its last caller has moved.
 */
@Singleton
class BookSyncRepository @Inject constructor(
    private val library: LibraryRepository,
    private val downloads: MediaDownloadRepository,
    private val positions: PositionRepository,
) {
    /** This device's stable id / display name, for position attribution. */
    val deviceId: String get() = positions.deviceId
    val deviceName: String get() = positions.deviceName

    /**
     * The application-scoped coroutine scope the detached position saves run
     * on — see [PositionRepository.appScope]. Exposed here so tests that drive
     * the facade can still substitute a `TestDispatcher`-backed scope.
     */
    internal var appScope: CoroutineScope
        get() = positions.appScope
        set(value) { positions.appScope = value }
    // ============ Library — delegated to LibraryRepository (issue #224) ============

    /** See [LibraryRepository.getPairsFlow]. */
    fun getPairsFlow(): Flow<List<BookPairEntity>> = library.getPairsFlow()

    /** See [LibraryRepository.getDownloadedPairsFlow]. */
    fun getDownloadedPairsFlow(): Flow<List<BookPairEntity>> = library.getDownloadedPairsFlow()

    /** See [LibraryRepository.fetchAllPages]. */
    internal suspend fun <T> fetchAllPages(fetchPage: suspend (page: Int) -> PageResponse<T>): List<T> =
        library.fetchAllPages(fetchPage)

    /** See [LibraryRepository.refreshPairs]. */
    suspend fun refreshPairs() = library.refreshPairs()

    /** See [LibraryRepository.getEbooksFlow]. */
    fun getEbooksFlow(): Flow<List<EBookEntity>> = library.getEbooksFlow()

    /** See [LibraryRepository.getDownloadedEbooksFlow]. */
    fun getDownloadedEbooksFlow(): Flow<List<EBookEntity>> = library.getDownloadedEbooksFlow()

    /** See [LibraryRepository.refreshEbooks]. */
    suspend fun refreshEbooks() = library.refreshEbooks()

    /** See [LibraryRepository.getAudiobooksFlow]. */
    fun getAudiobooksFlow(): Flow<List<AudioBookEntity>> = library.getAudiobooksFlow()

    /** See [LibraryRepository.getDownloadedAudiobooksFlow]. */
    fun getDownloadedAudiobooksFlow(): Flow<List<AudioBookEntity>> = library.getDownloadedAudiobooksFlow()

    /** See [LibraryRepository.getDownloadedAudiobooksAlphabeticalFlow]. */
    fun getDownloadedAudiobooksAlphabeticalFlow(): Flow<List<AudioBookEntity>> =
        library.getDownloadedAudiobooksAlphabeticalFlow()

    /** See [LibraryRepository.getRecentlyPlayedPairsFlow]. */
    fun getRecentlyPlayedPairsFlow(): Flow<List<BookPairEntity>> = library.getRecentlyPlayedPairsFlow()

    /** See [LibraryRepository.getRecentlyPlayedStandaloneAudiobooksFlow]. */
    fun getRecentlyPlayedStandaloneAudiobooksFlow(): Flow<List<AudioBookEntity>> =
        library.getRecentlyPlayedStandaloneAudiobooksFlow()

    /** See [LibraryRepository.getRecentlyReadEbooksFlow]. */
    fun getRecentlyReadEbooksFlow(): Flow<List<EBookEntity>> = library.getRecentlyReadEbooksFlow()

    /** See [LibraryRepository.lastOpenedTimesFlow]. */
    fun lastOpenedTimesFlow(): Flow<LastOpenedTimes> = library.lastOpenedTimesFlow()

    /** See [LibraryRepository.refreshAudiobooks]. */
    suspend fun refreshAudiobooks() = library.refreshAudiobooks()

    // ============ Pairing — delegated to LibraryRepository (issue #224) ============

    /** See [LibraryRepository.createPair]. */
    suspend fun createPair(ebookId: Int, audiobookId: Int) = library.createPair(ebookId, audiobookId)

    /** See [LibraryRepository.deletePair]. */
    suspend fun deletePair(pairId: Int) = library.deletePair(pairId)

    /** See [LibraryRepository.getUnpairedEbooks]. */
    suspend fun getUnpairedEbooks(): List<EBookEntity> = library.getUnpairedEbooks()

    /** See [LibraryRepository.getUnpairedAudiobooks]. */
    suspend fun getUnpairedAudiobooks(): List<AudioBookEntity> = library.getUnpairedAudiobooks()

    /** See [LibraryRepository.getPairById]. */
    suspend fun getPairById(pairId: Int): BookPairEntity? = library.getPairById(pairId)

    /** See [LibraryRepository.resolvePairOpenTarget]. */
    suspend fun resolvePairOpenTarget(pair: BookPairEntity, isOnline: Boolean): PairOpenTarget =
        library.resolvePairOpenTarget(pair, isOnline)

    /** See [LibraryRepository.resolvePairOpenTarget]. */
    suspend fun resolvePairOpenTarget(pairId: Int, isOnline: Boolean): PairOpenTarget =
        library.resolvePairOpenTarget(pairId, isOnline)

    /** See [LibraryRepository.progressSummaryForPair]. */
    suspend fun progressSummaryForPair(pair: BookPairEntity): ProgressSummary =
        library.progressSummaryForPair(pair)

    /** See [LibraryRepository.progressSummaryForEbook]. */
    suspend fun progressSummaryForEbook(ebookId: Int): ProgressSummary =
        library.progressSummaryForEbook(ebookId)

    /** See [LibraryRepository.progressSummaryForAudiobook]. */
    suspend fun progressSummaryForAudiobook(audiobookId: Int): ProgressSummary =
        library.progressSummaryForAudiobook(audiobookId)

    /** See [LibraryRepository.getEbookById]. */
    suspend fun getEbookById(ebookId: Int): EBookEntity? = library.getEbookById(ebookId)

    /** See [LibraryRepository.getAudiobookById]. */
    suspend fun getAudiobookById(audiobookId: Int): AudioBookEntity? = library.getAudiobookById(audiobookId)

    /** See [LibraryRepository.resolveEbookById]. */
    suspend fun resolveEbookById(ebookId: Int): EBookEntity? = library.resolveEbookById(ebookId)

    /** See [LibraryRepository.resolveAudiobookById]. */
    suspend fun resolveAudiobookById(audiobookId: Int): AudioBookEntity? = library.resolveAudiobookById(audiobookId)

    /** See [LibraryRepository.resolvePairById]. */
    suspend fun resolvePairById(pairId: Int): BookPairEntity? = library.resolvePairById(pairId)

    /** See [LibraryRepository.getPairByIdFlow]. */
    fun getPairByIdFlow(pairId: Int): Flow<BookPairEntity?> = library.getPairByIdFlow(pairId)

    /** See [LibraryRepository.getEbookByIdFlow]. */
    fun getEbookByIdFlow(ebookId: Int): Flow<EBookEntity?> = library.getEbookByIdFlow(ebookId)

    /** See [LibraryRepository.getAudiobookByIdFlow]. */
    fun getAudiobookByIdFlow(audiobookId: Int): Flow<AudioBookEntity?> = library.getAudiobookByIdFlow(audiobookId)

    /** See [LibraryRepository.searchLibrary]. */
    suspend fun searchLibrary(query: String): SearchResponse = library.searchLibrary(query)

    // ============ Downloads — delegated to MediaDownloadRepository (issue #224) ============

    /** See [MediaDownloadRepository.downloadEbook]. */
    suspend fun downloadEbook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File =
        downloads.downloadEbook(pair, onProgress)

    /** See [MediaDownloadRepository.downloadStandaloneEbook]. */
    suspend fun downloadStandaloneEbook(ebook: EBookEntity, onProgress: (Int) -> Unit = {}): File =
        downloads.downloadStandaloneEbook(ebook, onProgress)

    /** See [MediaDownloadRepository.downloadAudiobook]. */
    suspend fun downloadAudiobook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File =
        downloads.downloadAudiobook(pair, onProgress)

    /** See [MediaDownloadRepository.downloadStandaloneAudiobook]. */
    suspend fun downloadStandaloneAudiobook(audio: AudioBookEntity, onProgress: (Int) -> Unit = {}): File =
        downloads.downloadStandaloneAudiobook(audio, onProgress)

    /** See [MediaDownloadRepository.resetSyncMapDownloaded]. */
    suspend fun resetSyncMapDownloaded(pairId: Int) = downloads.resetSyncMapDownloaded(pairId)

    /** See [MediaDownloadRepository.downloadSyncMap]. */
    suspend fun downloadSyncMap(pairId: Int) = downloads.downloadSyncMap(pairId)

    /** See [MediaDownloadRepository.ensureSyncMapCached]. */
    suspend fun ensureSyncMapCached(pairId: Int): Boolean = downloads.ensureSyncMapCached(pairId)

    /** See [MediaDownloadRepository.downloadSyncMapWithRetry]. */
    suspend fun downloadSyncMapWithRetry(pairId: Int): Boolean = downloads.downloadSyncMapWithRetry(pairId)

    /** See [MediaDownloadRepository.localEbookFile]. */
    fun localEbookFile(serverFilename: String): File? = downloads.localEbookFile(serverFilename)

    /** See [MediaDownloadRepository.localAudioFile]. */
    fun localAudioFile(serverFilename: String): File? = downloads.localAudioFile(serverFilename)

    /** See [MediaDownloadRepository.getEbookFile]. */
    fun getEbookFile(pair: BookPairEntity): File = downloads.getEbookFile(pair)

    /** See [MediaDownloadRepository.getAudiobookFile]. */
    fun getAudiobookFile(pair: BookPairEntity): File = downloads.getAudiobookFile(pair)

    /** See [MediaDownloadRepository.getStandaloneEbookFile]. */
    fun getStandaloneEbookFile(ebook: EBookEntity): File = downloads.getStandaloneEbookFile(ebook)

    /** See [MediaDownloadRepository.deleteEbook]. */
    suspend fun deleteEbook(pair: BookPairEntity) = downloads.deleteEbook(pair)

    /** See [MediaDownloadRepository.deleteAudiobook]. */
    suspend fun deleteAudiobook(pair: BookPairEntity) = downloads.deleteAudiobook(pair)

    /** See [MediaDownloadRepository.deleteStandaloneEbook]. */
    suspend fun deleteStandaloneEbook(ebook: EBookEntity) = downloads.deleteStandaloneEbook(ebook)

    /** See [MediaDownloadRepository.deleteStandaloneAudiobook]. */
    suspend fun deleteStandaloneAudiobook(audio: AudioBookEntity) = downloads.deleteStandaloneAudiobook(audio)

    // ============ Positions — delegated to PositionRepository (issue #224) ============
    //
    // Everything under docs/position-sync-contract.md lives in [PositionRepository];
    // these one-liners keep every existing caller compiling. Defaults are copied
    // verbatim so a caller passing fewer arguments gets the same behaviour.

    /** See [PositionRepository.markComplete]. */
    suspend fun markComplete(mediaType: String, mediaId: Int) = positions.markComplete(mediaType, mediaId)

    /** See [PositionRepository.markPairComplete]. */
    suspend fun markPairComplete(pairId: Int, ebookId: Int, audiobookId: Int) =
        positions.markPairComplete(pairId, ebookId, audiobookId)

    /** See [PositionRepository.resetStandaloneProgress]. */
    suspend fun resetStandaloneProgress(mediaType: String, mediaId: Int): Boolean =
        positions.resetStandaloneProgress(mediaType, mediaId)

    /** See [PositionRepository.resetPairProgress]. */
    suspend fun resetPairProgress(pairId: Int): Boolean = positions.resetPairProgress(pairId)

    /** See [PositionRepository.getBookmark]. */
    suspend fun getBookmark(pairId: Int): BookmarkEntity? = positions.getBookmark(pairId)

    /** See [PositionRepository.getProgressOnce]. */
    suspend fun getProgressOnce(mediaType: String, mediaId: Int): UserProgressEntity? =
        positions.getProgressOnce(mediaType, mediaId)

    /** See [PositionRepository.getSyncPoints]. */
    suspend fun getSyncPoints(pairId: Int): List<SyncPointEntity> = positions.getSyncPoints(pairId)

    /** See [PositionRepository.getBookmarkFlow]. */
    fun getBookmarkFlow(pairId: Int): Flow<BookmarkEntity?> = positions.getBookmarkFlow(pairId)

    /** See [PositionRepository.refreshBookmark]. */
    suspend fun refreshBookmark(pairId: Int) = positions.refreshBookmark(pairId)

    /** See [PositionRepository.getBookmarkHistory]. */
    suspend fun getBookmarkHistory(pairId: Int, limit: Int = 50): List<BookmarkLogResponse> =
        positions.getBookmarkHistory(pairId, limit)

    /** See [PositionRepository.fetchPosition]. */
    suspend fun fetchPosition(pathScope: String, id: Int): PositionFetch = positions.fetchPosition(pathScope, id)

    /** See [PositionRepository.updatePosition]. */
    suspend fun updatePosition(pathScope: String, id: Int, request: PositionUpdateRequest): PositionResponse? =
        positions.updatePosition(pathScope, id, request)

    /** See [PositionRepository.savePlaybackPosition]. */
    suspend fun savePlaybackPosition(
        pairId: Int,
        audioPositionMs: Int,
        appendToLog: Boolean = false,
        claimFormat: Boolean = true,
        pushToServer: Boolean = true,
    ): Boolean = positions.savePlaybackPosition(
        pairId = pairId,
        audioPositionMs = audioPositionMs,
        appendToLog = appendToLog,
        claimFormat = claimFormat,
        pushToServer = pushToServer,
    )

    /** See [PositionRepository.savePlaybackPositionStandalone]. */
    suspend fun savePlaybackPositionStandalone(
        audiobookId: Int,
        audioPositionMs: Int,
        claimFormat: Boolean = true,
        pushToServer: Boolean = true,
    ): Boolean = positions.savePlaybackPositionStandalone(
        audiobookId = audiobookId,
        audioPositionMs = audioPositionMs,
        claimFormat = claimFormat,
        pushToServer = pushToServer,
    )

    /** See [PositionRepository.savePlaybackPositionDetached]. */
    fun savePlaybackPositionDetached(
        pairId: Int,
        audioPositionMs: Int,
        appendToLog: Boolean = false,
        claimFormat: Boolean = true,
    ): Job = positions.savePlaybackPositionDetached(
        pairId = pairId,
        audioPositionMs = audioPositionMs,
        appendToLog = appendToLog,
        claimFormat = claimFormat,
    )

    /** See [PositionRepository.savePlaybackPositionStandaloneDetached]. */
    fun savePlaybackPositionStandaloneDetached(
        audiobookId: Int,
        audioPositionMs: Int,
        claimFormat: Boolean = true,
    ): Job = positions.savePlaybackPositionStandaloneDetached(
        audiobookId = audiobookId,
        audioPositionMs = audioPositionMs,
        claimFormat = claimFormat,
    )

    /** See [PositionRepository.saveReaderPositionStandalone]. */
    suspend fun saveReaderPositionStandalone(
        ebookId: Int,
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        epubTextPreview: String? = null,
        epubProgressPercent: Float? = null,
        epubLocator: String? = null,
        claimFormat: Boolean = true,
        pushToServer: Boolean = true,
    ): Boolean = positions.saveReaderPositionStandalone(
        ebookId = ebookId,
        epubChapter = epubChapter,
        epubSentenceIndex = epubSentenceIndex,
        epubTextPreview = epubTextPreview,
        epubProgressPercent = epubProgressPercent,
        epubLocator = epubLocator,
        claimFormat = claimFormat,
        pushToServer = pushToServer,
    )

    /** See [PositionRepository.saveReaderPositionStandaloneDetached]. */
    fun saveReaderPositionStandaloneDetached(
        ebookId: Int,
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        epubTextPreview: String? = null,
        epubProgressPercent: Float? = null,
        epubLocator: String? = null,
        claimFormat: Boolean = true,
    ): Job = positions.saveReaderPositionStandaloneDetached(
        ebookId = ebookId,
        epubChapter = epubChapter,
        epubSentenceIndex = epubSentenceIndex,
        epubTextPreview = epubTextPreview,
        epubProgressPercent = epubProgressPercent,
        epubLocator = epubLocator,
        claimFormat = claimFormat,
    )

    /** See [PositionRepository.updateBookmark]. */
    suspend fun updateBookmark(
        pairId: Int,
        source: String,
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        syncMapVersion: Int? = null,
        audioPositionMs: Int? = null,
        epubLocator: String? = null,
        locatorAudioMs: Int? = null,
        appendToLog: Boolean = false,
        pushToServer: Boolean = true,
        stampSource: Boolean = true,
        markSynced: Boolean = true,
    ) = positions.updateBookmark(
        pairId = pairId,
        source = source,
        epubChapter = epubChapter,
        epubSentenceIndex = epubSentenceIndex,
        syncMapVersion = syncMapVersion,
        audioPositionMs = audioPositionMs,
        epubLocator = epubLocator,
        locatorAudioMs = locatorAudioMs,
        appendToLog = appendToLog,
        pushToServer = pushToServer,
        stampSource = stampSource,
        markSynced = markSynced,
    )

    /** See [PositionRepository.saveReaderPosition]. */
    fun saveReaderPosition(snapshot: ReaderPositionSnapshot): Job = positions.saveReaderPosition(snapshot)

    /** See [PositionRepository.updateBookmarkMetadata]. */
    suspend fun updateBookmarkMetadata(pairId: Int, source: String) = positions.updateBookmarkMetadata(pairId, source)

    /** See [PositionRepository.updateBookmarkLocator]. */
    suspend fun updateBookmarkLocator(pairId: Int, locatorJson: String, audioMs: Int? = null) =
        positions.updateBookmarkLocator(pairId, locatorJson, audioMs)

    /** See [PositionRepository.getSyncPointForEpubText]. */
    suspend fun getSyncPointForEpubText(pairId: Int, chapter: Int, epubText: String): SyncPointEntity? =
        positions.getSyncPointForEpubText(pairId, chapter, epubText)

    /** See [PositionRepository.getSentenceIndexFromProgression]. */
    suspend fun getSentenceIndexFromProgression(pairId: Int, chapter: Int, progression: Float): Int =
        positions.getSentenceIndexFromProgression(pairId, chapter, progression)

    /** See [PositionRepository.epubToAudioText]. */
    suspend fun epubToAudioText(
        pairId: Int,
        chapter: Int,
        epubText: String,
        rewindMs: Int = PlaybackOffsets.RESUME_REWIND_MS.toInt(),
    ): Int = positions.epubToAudioText(pairId, chapter, epubText, rewindMs)

    /** See [PositionRepository.audioToEpubText]. */
    suspend fun audioToEpubText(pairId: Int, audioPositionMs: Int): Pair<Int, String> =
        positions.audioToEpubText(pairId, audioPositionMs)

    /** See [PositionRepository.epubTextForSentence]. */
    suspend fun epubTextForSentence(pairId: Int, chapter: Int, sentenceIndex: Int?): String =
        positions.epubTextForSentence(pairId, chapter, sentenceIndex)

    /** See [PositionRepository.getProgressFlow]. */
    fun getProgressFlow(mediaType: String, mediaId: Int): Flow<UserProgressEntity?> =
        positions.getProgressFlow(mediaType, mediaId)

    /** See [PositionRepository.refreshProgress]. */
    suspend fun refreshProgress(mediaType: String, mediaId: Int) = positions.refreshProgress(mediaType, mediaId)

    /** See [PositionRepository.updateProgress]. */
    suspend fun updateProgress(
        mediaType: String,
        mediaId: Int,
        bookPairId: Int? = null,
        epubCfi: String? = null,
        epubChapter: Int? = null,
        epubProgressPercent: Float? = null,
        audioPositionMs: Int? = null,
        isCompleted: Boolean? = null,
        deviceId: String? = this.deviceId,
        pushToServer: Boolean = true,
        markSynced: Boolean = true,
    ) = positions.updateProgress(
        mediaType = mediaType,
        mediaId = mediaId,
        bookPairId = bookPairId,
        epubCfi = epubCfi,
        epubChapter = epubChapter,
        epubProgressPercent = epubProgressPercent,
        audioPositionMs = audioPositionMs,
        isCompleted = isCompleted,
        deviceId = deviceId,
        pushToServer = pushToServer,
        markSynced = markSynced,
    )

    /** See [PositionRepository.syncAllBookmarksAndProgress]. */
    suspend fun syncAllBookmarksAndProgress(pairs: List<BookPairEntity>) = positions.syncAllBookmarksAndProgress(pairs)

    /** See [PositionRepository.processPendingSync]. */
    suspend fun processPendingSync() = positions.processPendingSync()

    // ============ New Items — delegated to LibraryRepository (issue #224) ============

    /** See [LibraryRepository.getNewEbooksFlow]. */
    fun getNewEbooksFlow(): Flow<List<EBookEntity>> = library.getNewEbooksFlow()

    /** See [LibraryRepository.getNewAudiobooksFlow]. */
    fun getNewAudiobooksFlow(): Flow<List<AudioBookEntity>> = library.getNewAudiobooksFlow()

    /** See [LibraryRepository.getNewPairsFlow]. */
    fun getNewPairsFlow(): Flow<List<BookPairEntity>> = library.getNewPairsFlow()

    /** See [LibraryRepository.getNewEbookCountFlow]. */
    fun getNewEbookCountFlow(): Flow<Int> = library.getNewEbookCountFlow()

    /** See [LibraryRepository.getNewAudiobookCountFlow]. */
    fun getNewAudiobookCountFlow(): Flow<Int> = library.getNewAudiobookCountFlow()

    /** See [LibraryRepository.getNewPairCountFlow]. */
    fun getNewPairCountFlow(): Flow<Int> = library.getNewPairCountFlow()

    /** See [LibraryRepository.acknowledgeItems]. */
    suspend fun acknowledgeItems(ids: List<Int>, type: String) = library.acknowledgeItems(ids, type)
}
