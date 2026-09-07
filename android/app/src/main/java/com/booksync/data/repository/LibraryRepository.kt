package com.booksync.data.repository

import com.booksync.data.local.dao.*
import com.booksync.data.local.entity.*
import com.booksync.data.remote.UserScopeProvider
import com.booksync.data.remote.*
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import javax.inject.Inject
import javax.inject.Singleton

private const val REPO_TAG = "LibraryRepository"
// Page size for full-library refreshes (issue #48) — the server's maximum.
private const val LIBRARY_PAGE_SIZE = 500

/** Where a tap on a paired book should land. */
enum class PairOpenTarget { Reader, Player, Details }

/**
 * Last-opened moments, epoch millis, split by what the id refers to (issue #223).
 *
 * Three maps rather than one because the ids are not unique across kinds: pair 7,
 * ebook 7 and audiobook 7 are three different books. Absent means never opened;
 * a stored `0L` means the timestamp could not be parsed and is treated the same
 * way by [com.booksync.ui.library.lastOpenedFor].
 */
data class LastOpenedTimes(
    val pairs: Map<Int, Long> = emptyMap(),
    val ebooks: Map<Int, Long> = emptyMap(),
    val audiobooks: Map<Int, Long> = emptyMap(),
)

/**
 * The library catalogue (issue #224): the Room mirror of the server's pairs,
 * ebooks and audiobooks and the refreshes that keep it current, pairing and
 * per-item lookup, the Continue / Recently-opened flows the Home and Library
 * screens read, remote search, and the NEW-item acknowledgements of issue
 * #222. Reads prefer the local cache; the refresh* functions mirror the whole
 * server listing and delete orphans, so they never act on a partial page.
 *
 * [BookSyncRepository] delegates to this class so existing callers are
 * unchanged.
 */
@Singleton
class LibraryRepository @Inject constructor(
    private val api: BookSyncApi,
    private val bookPairDao: BookPairDao,
    private val eBookDao: EBookDao,
    private val audioBookDao: AudioBookDao,
    private val syncPointDao: SyncPointDao,
    private val bookmarkDao: BookmarkDao,
    private val userProgressDao: UserProgressDao,
    private val acknowledgedItemDao: AcknowledgedItemDao,
    private val diagnosticLogger: DiagnosticLogger,
    private val userScopeProvider: UserScopeProvider,
) {
    /**
     * The account every cache query below is scoped to (issue #314).
     *
     * When no account resolves — signed out, or no server configured — this is a
     * sentinel that matches no row, so a read returns nothing rather than
     * another account's reading data. Reads are the common case in that state
     * (the app is on the login screen); the write paths that matter check
     * [scopeKeyOrNull] and hold their data instead of writing it somewhere it
     * would never be read from again.
     */
    private val scope: String get() = userScopeProvider.currentKey ?: NO_SCOPE

    /** Null when no account resolves — for callers that must not guess. */
    private val scopeKeyOrNull: String? get() = userScopeProvider.currentKey

    private fun log(msg: String) = diagnosticLogger.i(LogChannel.APP, REPO_TAG, msg)

    // ============ Library ============

    /** Get all book pairs as a reactive Flow from local cache. */
    fun getPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getAllPairs()

    /** Get downloaded book pairs as a reactive Flow from local cache. */
    fun getDownloadedPairsFlow(): Flow<List<BookPairEntity>> = bookPairDao.getDownloadedPairs()

    /**
     * Walk every page of a paginated list endpoint (issue #48) and return the
     * whole set. The refresh* functions below mirror the server into Room and
     * finish with `deleteOrphansExcept(remoteIds)`, so they must never act on
     * a partial listing — a failure mid-walk throws before anything is written.
     */
    internal suspend fun <T> fetchAllPages(fetchPage: suspend (page: Int) -> PageResponse<T>): List<T> {
        val all = mutableListOf<T>()
        var page = 1
        while (true) {
            val body = fetchPage(page)
            all += body.items
            if (body.items.size < body.limit || all.size >= body.total) break
            page++
        }
        return all
    }

    /** Refresh book pairs from the server and update local cache. */
    suspend fun refreshPairs() {
        log("refreshPairs — fetching from server")
        val remotePairs = fetchAllPages { page -> api.getPairs(page, LIBRARY_PAGE_SIZE) }
        val entities = remotePairs.map { pair ->
            val existing = bookPairDao.getPairById(pair.id)

            // A re-transcription rebuilds the server's sync map with new audio
            // timestamps under a new version (issue #55). Cached points from the
            // old one aren't merely out of date — `epubToAudioText` still finds
            // the right text and hands back a second that no longer exists — so
            // drop them outright rather than flagging them.
            //
            // A null *remote* version is "unknown", not "no map" — an endpoint
            // that didn't load the relationship reports null, and wiping a good
            // cache on that would be worse than carrying it. A null *cached*
            // version on points that are present is different: it means a build
            // predating this column downloaded them and we cannot tell which map
            // they are, so they get refetched once. "Probably still fine" is the
            // reasoning that produced this bug.
            val remoteVersion = pair.sync_map_version
            val cachedVersion = existing?.syncMapVersion
            val cacheIsStale = remoteVersion != null &&
                existing?.syncMapDownloaded == true &&
                remoteVersion != cachedVersion
            if (cacheIsStale) {
                log("refreshPairs — pair ${pair.id} sync map v$cachedVersion -> v$remoteVersion; dropping cached points")
                syncPointDao.deletePointsForPair(pair.id)
            }

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
                syncMapDownloaded = if (cacheIsStale) false else existing?.syncMapDownloaded ?: false,
                syncMapVersion = if (cacheIsStale) null else existing?.syncMapVersion,
                audiobookCoverPath = pair.audiobook.cover_path ?: existing?.audiobookCoverPath,
                ebookSeries = pair.ebook.series,
                ebookSeriesIndex = pair.ebook.series_index,
            )
        }
        bookPairDao.upsertPairs(entities)
        val remoteIds = remotePairs.map { it.id }
        if (remoteIds.isEmpty()) bookPairDao.deleteAll() else bookPairDao.deleteOrphansExcept(remoteIds)
        seedAcknowledged(remotePairs.filter { it.acknowledged }.map { it.id }, "pair")
        log("refreshPairs — saved ${entities.size} pairs to cache")
    }
    
    /** Get all ebooks as a reactive Flow from local cache. */
    fun getEbooksFlow(): Flow<List<EBookEntity>> = eBookDao.getAllEBooks()

    /** Get downloaded ebooks as a reactive Flow from local cache. */
    fun getDownloadedEbooksFlow(): Flow<List<EBookEntity>> = eBookDao.getDownloadedEBooks()

    /** Refresh ebooks from the server and update local cache. */
    suspend fun refreshEbooks() {
        log("refreshEbooks — fetching from server")
        val remoteEbooks = fetchAllPages { page -> api.getEbooks(page, LIBRARY_PAGE_SIZE) }
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
        seedAcknowledged(remoteEbooks.filter { it.acknowledged }.map { it.id }, "ebook")
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
            bookPairDao.getRecentlyPlayedPairs(scope),
            userProgressDao.getAllProgressFlow(scope),
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
            audioBookDao.getRecentlyPlayedStandaloneAudiobooks(scope),
            userProgressDao.getAllProgressFlow(scope),
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
            eBookDao.getRecentlyReadEbooks(scope),
            userProgressDao.getAllProgressFlow(scope),
        ) { ebooks, allProgress ->
            val completedIds = allProgress
                .filter { it.mediaType == "ebook" && it.isCompleted }
                .map { it.mediaId }
                .toSet()
            ebooks.filter { it.id !in completedIds }
        }

    /**
     * When each library item was last opened, epoch millis, for the library's
     * "Recently opened" sort (issue #223).
     *
     * Two tables, because the app writes a position to different places
     * depending on what is being read: a pair's write lands in `bookmarks`
     * (`updatedAt` / `capturedAt`, ISO-8601 strings, keyed by `bookPairId`),
     * a standalone ebook's or audiobook's in `user_progress` (`updatedAt`,
     * epoch millis, keyed by `mediaType`/`mediaId`). Reading one table would
     * leave half the library looking never-opened, which is close to what the
     * old id-descending "proxy" comparator did.
     *
     * `capturedAt` wins over `updatedAt` where present, the same preference the
     * sync conflict resolution uses (issue #54): it is when the position was
     * really recorded, as opposed to when a row was last touched.
     */
    fun lastOpenedTimesFlow(): Flow<LastOpenedTimes> =
        combine(
            bookmarkDao.getAllBookmarksFlow(scope),
            userProgressDao.getAllProgressFlow(scope),
        ) { bookmarks, progress ->
            LastOpenedTimes(
                pairs = bookmarks.associate {
                    it.bookPairId to parseSyncTimestamp(preferCapturedAt(it.capturedAt, it.updatedAt))
                },
                ebooks = progress.filter { it.mediaType == "ebook" }
                    .associate { it.mediaId to it.updatedAt },
                audiobooks = progress.filter { it.mediaType == "audiobook" }
                    .associate { it.mediaId to it.updatedAt },
            )
        }

    /** Refresh audiobooks from the server and update local cache. */
    suspend fun refreshAudiobooks() {
        log("refreshAudiobooks — fetching from server")
        val remoteAudiobooks = fetchAllPages { page -> api.getAudiobooks(page, LIBRARY_PAGE_SIZE) }
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
        seedAcknowledged(remoteAudiobooks.filter { it.acknowledged }.map { it.id }, "audiobook")
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
        val source = bookmarkDao.getBookmark(scope, pair.id)?.source
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

    // ---- Cache first, server second (issue #338) --------------------------
    //
    // Search results come from the server, so a fresh install can offer to
    // download an id Room has never seen. `DownloadWorker` read Room and only
    // Room, so the tap died as `Result.failure("Audiobook not found in DB")`
    // before a single request was issued — visible in logcat and nowhere else.
    // Opening the Library tab and refreshing fixed it, which is not something a
    // user can be expected to guess.
    //
    // The two failure modes stay distinguishable on purpose:
    //   - null  — the server answered, and does not have this book. Final; the
    //             caller says so and stops.
    //   - throw — we could not ask (offline, 5xx, expired session). The
    //             worker's `classifyDownloadFailure` still gets to retry, and
    //             the message names the network rather than blaming the book.

    /** Room's copy of an ebook, fetching and caching it from the server if absent. */
    suspend fun resolveEbookById(ebookId: Int): EBookEntity? =
        eBookDao.getEBookById(ebookId) ?: fetchEbookIntoCache(ebookId)

    /** Room's copy of an audiobook, fetching and caching it from the server if absent. */
    suspend fun resolveAudiobookById(audiobookId: Int): AudioBookEntity? =
        audioBookDao.getAudioBookById(audiobookId) ?: fetchAudiobookIntoCache(audiobookId)

    /**
     * Room's copy of a pair, refreshing the whole pair list if absent.
     *
     * There is no single-pair endpoint on the server, so this reuses the
     * refresh a Library pull already runs rather than inventing a second
     * mapping of `BookPairResponse` that would drift from [refreshPairs].
     */
    suspend fun resolvePairById(pairId: Int): BookPairEntity? =
        bookPairDao.getPairById(pairId) ?: run {
            log("resolvePairById — pair $pairId not cached; refreshing pairs")
            refreshPairs()
            bookPairDao.getPairById(pairId)
        }

    private suspend fun fetchEbookIntoCache(ebookId: Int): EBookEntity? {
        log("resolveEbookById — ebook $ebookId not cached; asking the server")
        val remote = getOrNullOn404 { api.getEbook(ebookId) } ?: return null
        val entity = EBookEntity(
            id = remote.id,
            title = remote.title,
            author = remote.author,
            filename = remote.filename,
            fileSize = remote.file_size,
            format = remote.format,
            series = remote.series,
            seriesIndex = remote.series_index,
            uploadedAt = remote.uploaded_at,
            isDownloaded = false,
        )
        eBookDao.upsertEBooks(listOf(entity))
        return entity
    }

    private suspend fun fetchAudiobookIntoCache(audiobookId: Int): AudioBookEntity? {
        log("resolveAudiobookById — audiobook $audiobookId not cached; asking the server")
        val remote = getOrNullOn404 { api.getAudiobook(audiobookId) } ?: return null
        val entity = AudioBookEntity(
            id = remote.id,
            title = remote.title,
            author = remote.author,
            filename = remote.filename,
            durationSeconds = remote.duration_seconds,
            format = remote.format,
            series = remote.series,
            seriesIndex = remote.series_index,
            uploadedAt = remote.uploaded_at,
            isDownloaded = false,
            coverFilename = remote.cover_path,
        )
        audioBookDao.upsertAudioBooks(listOf(entity))
        return entity
    }

    /** 404 is an answer ("gone"); every other failure is a question we could not ask. */
    private suspend fun <T> getOrNullOn404(fetch: suspend () -> T): T? =
        try {
            fetch()
        } catch (e: retrofit2.HttpException) {
            if (e.code() == 404) null else throw e
        }

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

    // ============ New Items (unacknowledged) ============

    fun getNewEbooksFlow(): Flow<List<EBookEntity>> = acknowledgedItemDao.getNewEbooks(scope)
    fun getNewAudiobooksFlow(): Flow<List<AudioBookEntity>> = acknowledgedItemDao.getNewAudiobooks(scope)
    fun getNewPairsFlow(): Flow<List<BookPairEntity>> = acknowledgedItemDao.getNewPairs(scope)
    fun getNewEbookCountFlow(): Flow<Int> = acknowledgedItemDao.getNewEbookCount(scope)
    fun getNewAudiobookCountFlow(): Flow<Int> = acknowledgedItemDao.getNewAudiobookCount(scope)
    fun getNewPairCountFlow(): Flow<Int> = acknowledgedItemDao.getNewPairCount(scope)

    /**
     * Mark items as seen (issue #222).
     *
     * Local row first, then the server. The local write is what the NEW badge
     * reads, so writing it first keeps the tap instant and keeps working
     * offline; the push is what makes the same acknowledgement show up on the
     * web and on a second device, because `acknowledged` is a property of the
     * item on the server, not of the viewer.
     *
     * A failed push is swallowed on purpose (P3): the local row stands, the UI
     * never rolls back or shows an error, and the next library refresh simply
     * won't seed this id — the phone keeps its own acknowledgement either way.
     * There is deliberately no retry queue.
     */
    suspend fun acknowledgeItems(ids: List<Int>, type: String) {
        if (ids.isEmpty()) return
        acknowledgedItemDao.acknowledge(ids.map { AcknowledgedItemEntity(scope, it, type) })
        try {
            when (type) {
                "pair" -> api.acknowledgeNewPairs(AcknowledgePairsRequest(pair_ids = ids))
                "ebook" -> api.acknowledgeNewItems(AcknowledgeItemsRequest(ebook_ids = ids))
                "audiobook" -> api.acknowledgeNewItems(AcknowledgeItemsRequest(audiobook_ids = ids))
                else -> log("acknowledgeItems — unknown type '$type', not pushed")
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("acknowledgeItems — server push failed for $type (${ids.size} ids): ${e.message}; keeping local rows")
        }
    }

    /**
     * Seed [AcknowledgedItemEntity] rows from the server's `acknowledged` flag
     * during a library refresh (issue #222) — this is how an acknowledgement
     * made on the web reaches the phone.
     *
     * Insert-only: a server that reports an item as still new never removes a
     * local row, so an acknowledgement this device made while offline is not
     * resurrected as NEW by the next refresh.
     */
    private suspend fun seedAcknowledged(ids: List<Int>, type: String) {
        if (ids.isEmpty()) return
        val key = scopeKeyOrNull ?: return
        acknowledgedItemDao.acknowledge(ids.map { AcknowledgedItemEntity(key, it, type) })
    }
}
