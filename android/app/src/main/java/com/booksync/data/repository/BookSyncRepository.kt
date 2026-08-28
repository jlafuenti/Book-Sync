package com.booksync.data.repository

import com.booksync.sync.SyncMatcher

import android.content.Context
import com.booksync.data.local.dao.*
import com.booksync.data.local.entity.*
import com.booksync.data.remote.UserScopeProvider
import com.booksync.data.remote.*
import com.booksync.data.sync.HINT_READIUM_LOCATOR
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import com.booksync.player.PlaybackOffsets
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Main repository that coordinates between the remote API and local database.
 * Implements offline-first pattern: writes go to local + sync queue,
 * reads prefer local cache.
 */
private const val REPO_TAG = "BookSyncRepository"
// Page size for full-library refreshes (issue #48) — the server's maximum.
private const val LIBRARY_PAGE_SIZE = 500

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
    private val deviceIdManager: DeviceIdManager,
    private val json: Json,
    private val userScopeProvider: UserScopeProvider,
) {
    /**
     * The account this repository is reading and writing as (issue #314).
     *
     * Null when the signed-in user or the server cannot be determined. Callers
     * must skip rather than fall back to unscoped access — an unscoped read shows
     * another account's reading data, and an unscoped write replays into it.
     */
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

    private companion object {
        /**
         * Matches no row: [UserScope.of] always produces "server|id" with a
         * non-empty server, so this cannot collide with a real scope. Never
         * written to a row — only ever compared against.
         */
        const val NO_SCOPE = "<no-account>"
    }

    /** This device's stable id / display name, for position attribution. */
    val deviceId: String get() = deviceIdManager.deviceId
    val deviceName: String get() = deviceIdManager.deviceName

    /**
     * Serialises [processPendingSync]. Two callers can legitimately fire at
     * once — `SyncWorker` (periodic and connectivity-triggered) and
     * `LibraryViewModel` when the screen opens — and each reads the whole
     * queue up front. Without this they replay the same rows concurrently:
     * observed on a device as 713 replays of a 366-row queue, doubling server
     * writes and leaving the record briefly holding an older row's position
     * while the two streams interleaved. The second caller now waits, then
     * finds the queue already empty.
     */
    private val pendingSyncMutex = Mutex()

    /**
     * Application-scoped — outlives any Activity's `lifecycleScope`. The
     * reader's close-flush save used to run inside `lifecycleScope.launch`,
     * so back press -> onPause -> finish() cancelled that coroutine
     * mid-network-call and lost BOTH the server write and the local Room
     * write (issue #61/#40 — see [saveReaderPosition]). `internal var` so
     * tests can substitute a scope backed by a `TestDispatcher`'s scheduler
     * for deterministic assertions.
     */
    internal var appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private fun log(msg: String) = diagnosticLogger.i(LogChannel.APP, REPO_TAG, msg)
    private fun logW(msg: String) = diagnosticLogger.w(LogChannel.APP, REPO_TAG, msg)
    private fun logE(msg: String, t: Throwable? = null) = diagnosticLogger.e(LogChannel.APP, REPO_TAG, msg, t)
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
            audioBookDao.getRecentlyPlayedStandaloneAudiobooks(),
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
            eBookDao.getRecentlyReadEbooks(),
            userProgressDao.getAllProgressFlow(scope),
        ) { ebooks, allProgress ->
            val completedIds = allProgress
                .filter { it.mediaType == "ebook" && it.isCompleted }
                .map { it.mediaId }
                .toSet()
            ebooks.filter { it.id !in completedIds }
        }

    /** Mark a *standalone* media item as completed. Pairs go through [markPairComplete]. */
    suspend fun markComplete(mediaType: String, mediaId: Int) {
        updateProgress(mediaType, mediaId, isCompleted = true)
    }

    /**
     * Mark a pair finished: one `PUT /api/sync/position/pair/{id}` carrying
     * `is_completed = true` and nothing else (issue #56,
     * docs/position-sync-contract.md § Completion).
     *
     * This used to be two standalone-scope writes (`ebook/{id}` +
     * `audiobook/{id}`), which flagged the two standalone records and left the
     * pair's own canonical record un-finished — the web writes the pair scope,
     * so the two clients disagreed about the same book. The server projects a
     * pair-scoped flag onto both `user_progress` rows itself.
     *
     * The write carries no anchor and no `source`: a completion toggle must not
     * move the position or re-claim which format opens next. Both local rows
     * are flagged first (unsynced) so an offline toggle still shows on the
     * Finished shelf and the sync sweep delivers it later.
     */
    suspend fun markPairComplete(pairId: Int, ebookId: Int, audiobookId: Int) {
        val nowMillis = System.currentTimeMillis()
        val capturedAt = capturedAtIsoFromMillis(nowMillis)

        suspend fun flagLocal(mediaType: String, mediaId: Int, synced: Boolean) {
            val existing = userProgressDao.getProgress(scope, mediaType, mediaId)
            userProgressDao.upsertProgress(
                UserProgressEntity(
                    mediaType = mediaType,
                    mediaId = mediaId,
                    bookPairId = pairId,
                    epubCfi = existing?.epubCfi,
                    epubChapter = existing?.epubChapter,
                    epubProgressPercent = existing?.epubProgressPercent,
                    audioPositionMs = existing?.audioPositionMs,
                    isCompleted = true,
                    updatedAt = nowMillis,
                    deviceId = deviceIdManager.deviceId,
                    deviceName = deviceIdManager.deviceName,
                    capturedAt = capturedAt,
                        scopeKey = scope,
                    syncedToServer = synced,
                )
            )
        }

        flagLocal("ebook", ebookId, synced = false)
        flagLocal("audiobook", audiobookId, synced = false)

        val ok = try {
            api.updatePosition(
                "pair", pairId,
                PositionUpdateRequest(
                    is_completed = true,
                    device_id = deviceIdManager.deviceId,
                    device_name = deviceIdManager.deviceName,
                    captured_at = capturedAt,
                )
            ).isSuccessful
        } catch (e: Exception) {
            logW("markPairComplete $pairId: push failed (${e.message}) — left unsynced for the sweep")
            false
        }
        if (ok) {
            flagLocal("ebook", ebookId, synced = true)
            flagLocal("audiobook", audiobookId, synced = true)
        }
    }

    /**
     * Reset progress for a standalone (unpaired) ebook/audiobook (issue #103):
     * `DELETE /api/sync/position/{scope}/{id}` removes the canonical bookmark
     * (+ hints) and the progress projection server-side, so `GET /position`
     * answers 204 ("unread") again — the legacy zero-write this replaces only
     * pinned the position at 0 and left the bookmark in place to re-seed it.
     *
     * Same honesty contract as [resetPairProgress]: local cleanup only after
     * the server DELETE succeeds; on failure/offline nothing changes locally
     * and this returns `false` so the caller can tell the user the reset did
     * not happen. Local `bookmarks` and `pending_sync` are pair-keyed, so the
     * only local row to clear here is `user_progress`.
     */
    suspend fun resetStandaloneProgress(mediaType: String, mediaId: Int): Boolean {
        val response = try {
            api.resetPosition(mediaType, mediaId)
        } catch (e: Exception) {
            logW("resetStandaloneProgress $mediaType=$mediaId: offline (${e.message}) — leaving local state unchanged")
            return false
        }
        if (!response.isSuccessful) {
            logW("resetStandaloneProgress $mediaType=$mediaId: HTTP ${response.code()} — leaving local state unchanged")
            return false
        }
        userProgressDao.deleteProgress(scope, mediaType, mediaId)
        return true
    }

    /**
     * Reset ALL progress for a paired book: deletes the canonical bookmark (+
     * hints) and every user_progress row on the server, then clears the
     * matching local caches. The removed `resetMediaProgress` zero-write only
     * pins `epub_progress_percent`/`audio_position_ms` at 0 and leaves the
     * Bookmark row in place — the next sync (or even the next open) re-seeds
     * progress right back from it, so the reset silently un-resets itself
     * (same failure mode as server issue #6, now fixed pair-side by
     * `DELETE /api/sync/progress/pair/{pairId}`).
     *
     * Local cleanup only happens once the server DELETE actually succeeds
     * (issue #61/#40 fix 3). Clearing local state first (or on failure/offline)
     * would leave the reset half-applied on this device while the server
     * still holds the old position — and worse, an unsynced local
     * `user_progress` row could then be picked up by
     * `syncAllBookmarksAndProgress`/`processPendingSync` and pushed back to
     * the server, resurrecting exactly what the reset was supposed to clear.
     * On failure/offline this changes nothing locally and returns `false` so
     * the reset can honestly be reported as not having happened; callers may
     * ignore the result.
     */
    suspend fun resetPairProgress(pairId: Int): Boolean {
        val response = try {
            api.resetPairProgress(pairId)
        } catch (e: Exception) {
            logW("resetPairProgress pair=$pairId: offline (${e.message}) — leaving local state unchanged")
            return false
        }
        if (!response.isSuccessful) {
            logW("resetPairProgress pair=$pairId: HTTP ${response.code()} — leaving local state unchanged")
            return false
        }
        bookmarkDao.deleteBookmark(scope, pairId)
        pendingSyncDao.deleteForPair(scope, pairId)
        // Also clear the local user_progress projection for the pair's own
        // ebook/audiobook — a stale row here is exactly the resurrection
        // vector this fix closes (see the doc comment above).
        val pair = bookPairDao.getPairById(pairId)
        if (pair != null) {
            userProgressDao.deleteProgress(scope, "ebook", pair.ebookId)
            userProgressDao.deleteProgress(scope, "audiobook", pair.audiobookId)
        }
        return true
    }

    /** Get the current bookmark for a pair (single snapshot, not a flow). */
    suspend fun getBookmark(pairId: Int): com.booksync.data.local.entity.BookmarkEntity? =
        bookmarkDao.getBookmark(scope, pairId)

    /** Get user progress for a given media item (single snapshot). */
    suspend fun getProgressOnce(mediaType: String, mediaId: Int): com.booksync.data.local.entity.UserProgressEntity? =
        userProgressDao.getProgress(scope, mediaType, mediaId)

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

    /**
     * Shared streaming core for the four download entry points (issue #160):
     * status check, throw on empty body, buffered copy with de-duplicated
     * progress. The four functions were hand-copied ~35-line loops that had
     * already drifted — `downloadStandaloneAudiobook` lost the status check
     * entirely, so a 401/404/500 wrote nothing yet still flipped the
     * downloaded flag, and `downloadEbook` lost the progress de-dup.
     *
     * Streams into a `.part` sibling and renames on completion, so an
     * interrupted transfer never leaves a truncated file under the real name
     * (the player and buildCastMediaItem only check the file exists).
     */
    private suspend fun streamToFile(
        response: retrofit2.Response<okhttp3.ResponseBody>,
        target: File,
        onProgress: (Int) -> Unit,
    ): File {
        if (!response.isSuccessful) {
            logE("download failed — HTTP ${response.code()} for ${target.name}")
            throw Exception("HTTP ${response.code()}: ${response.errorBody()?.string()}")
        }
        val body = response.body() ?: throw Exception("Empty response body")
        target.parentFile?.mkdirs()
        val part = File(target.parentFile, target.name + ".part")
        withContext(Dispatchers.IO) {
            try {
                val contentLength = body.contentLength()
                body.byteStream().use { input ->
                    part.outputStream().use { output ->
                        val buffer = ByteArray(8 * 1024)
                        var bytesCopied = 0L
                        var lastProgress = -1
                        var bytes = input.read(buffer)
                        while (bytes >= 0) {
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
                if (!part.renameTo(target)) {
                    part.copyTo(target, overwrite = true)
                    part.delete()
                }
            } catch (e: Exception) {
                part.delete()
                throw e
            }
        }
        return target
    }

    /** Download the ebook file for a book pair. */
    suspend fun downloadEbook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        log("downloadEbook — pairId=${pair.id} file=${pair.ebookFilename}")
        val file = streamToFile(
            api.downloadEbook(pair.ebookId),
            File(File(context.filesDir, "ebooks"), pair.ebookFilename),
            onProgress,
        )
        bookPairDao.setEbookDownloaded(pair.id, true)
        log("downloadEbook complete — ${file.length() / 1024}KB")
        return file
    }

    /** Download a standalone ebook file. */
    suspend fun downloadStandaloneEbook(ebook: EBookEntity, onProgress: (Int) -> Unit = {}): File {
        val file = streamToFile(
            api.downloadEbook(ebook.id),
            File(File(context.filesDir, "ebooks"), ebook.filename),
            onProgress,
        )
        eBookDao.setDownloaded(ebook.id, true)
        return file
    }

    /** Download the audiobook file for a book pair. */
    suspend fun downloadAudiobook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        log("downloadAudiobook — pairId=${pair.id} file=${pair.audiobookFilename}")
        val file = streamToFile(
            api.downloadAudiobook(pair.audiobookId),
            File(File(context.filesDir, "audiobooks"), pair.audiobookFilename),
            onProgress,
        )
        bookPairDao.setAudiobookDownloaded(pair.id, true)
        log("downloadAudiobook complete — ${file.length() / 1024}KB")
        return file
    }

    /** Download a standalone audiobook file. */
    suspend fun downloadStandaloneAudiobook(audio: AudioBookEntity, onProgress: (Int) -> Unit = {}): File {
        val file = streamToFile(
            api.downloadAudiobook(audio.id),
            File(File(context.filesDir, "audiobooks"), audio.filename),
            onProgress,
        )
        audioBookDao.setDownloaded(audio.id, true)
        return file
    }

    /** Reset the syncMapDownloaded flag so a re-download is triggered. */
    suspend fun resetSyncMapDownloaded(pairId: Int) {
        // Clear the version alongside the flag: a cache marked absent that still
        // claims a version would tell `refreshPairs` it is current.
        bookPairDao.setSyncMapCached(pairId, false, null)
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
        // Stamp the version in the same statement that marks the cache present:
        // a cache that reads as downloaded but doesn't say which map it holds is
        // exactly the state issue #55 is about.
        bookPairDao.setSyncMapCached(pairId, true, syncMap.version)
        log("downloadSyncMap complete — ${entities.size} sync points saved (v${syncMap.version})")
    }

    /**
     * Make sure this pair's cached sync points are present, fetching them if a
     * version bump caused [refreshPairs] to drop them. Best-effort: returns
     * false and never throws when the map can't be fetched.
     *
     * Called from the paths that are about to *read* sync points — the reader,
     * the player, Android Auto resume — so recovery from a re-transcription
     * needs no user action. Cheap when the cache is current: one Room read.
     *
     * Deliberately a *single* attempt rather than [downloadSyncMapWithRetry]:
     * these callers are opening a screen or starting playback, and the retry
     * wrapper's 1s/3s/10s backoff would sit in front of that. Persistence is the
     * `DownloadWorker`'s job; here a miss just means the next open tries again.
     */
    suspend fun ensureSyncMapCached(pairId: Int): Boolean {
        if (bookPairDao.getPairById(pairId)?.syncMapDownloaded == true) return true
        log("ensureSyncMapCached — pair $pairId has no cached sync map; fetching")
        return try {
            downloadSyncMap(pairId)
            true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            logW("ensureSyncMapCached — pair $pairId fetch failed: ${e.message}")
            false
        }
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
        bookmarkDao.getBookmarkFlow(scope, pairId)

    /** Refresh the bookmark from the server, respecting unsynced local data. */
    suspend fun refreshBookmark(pairId: Int) {
        // A 204 (never opened) and an unreachable server both come back null —
        // in either case there is nothing to pull and the local cache stands.
        val remote = fetchPosition("pair", pairId).position ?: return
        val existing = bookmarkDao.getBookmark(scope, pairId)

        // Never overwrite unsynced local data — offline progress must survive
        if (existing != null && !existing.syncedToServer) {
            log("refreshBookmark pair=$pairId: local has unsynced changes, skipping server pull")
            return
        }

        // Only pull if server is newer or no local data exists
        val remoteTs = parseSyncTimestamp(remote.updated_at)
        val localTs = parseSyncTimestamp(existing?.updatedAt)

        if (existing == null || remoteTs >= localTs) {
            bookmarkDao.upsertBookmark(remote.toBookmarkEntity(scope, pairId, existing))
        } else {
            log("refreshBookmark pair=$pairId: local is newer (local=$localTs, remote=$remoteTs), keeping local")
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
                val existingLocalId = bookmarkLogDao.findByServerId(scope, pairId, entry.id)
                val row = entry.toEntity(scope, pairId).copy(localId = existingLocalId ?: 0L)
                if (existingLocalId != null) bookmarkLogDao.update(row) else bookmarkLogDao.insertLocal(row)
            }
            serverEntries.maxByOrNull { it.changed_at }?.let { newest ->
                bookmarkLogDao.deleteLocalOnlyOlderThan(scope, pairId, newest.changed_at)
            }
        } catch (e: Exception) {
            logW("getBookmarkHistory offline — using local cache (${e.message})")
        }
        return bookmarkLogDao.getForPair(scope, pairId, limit).map { it.toResponse() }
    }

    // ============ Multi-device conflict resolution (issue #54) ============
    //
    // Both `pushBookmark`/`pushProgress` centralize the same three-way outcome so every
    // call site (interactive write, startup bidirectional sync, offline-queue replay)
    // handles a 409 identically: the write was NOT applied server-side because a newer
    // position (by `captured_at`) already exists there. We drop our write and adopt the
    // server's authoritative state locally rather than retrying it.

    /** Outcome of pushing a bookmark or progress write to the server. */
    private enum class PushOutcome { SYNCED, CONFLICT_ADOPTED, FAILED }

    /** Parses a 409 (or any) JSON error body into [T] using the app's shared [Json], or null
     *  if the body is missing/unparseable. Errors are logged, never thrown — a conflict whose
     *  body we can't parse still means "don't retry this write", just without local adoption. */
    private inline fun <reified T> parseConflictBody(raw: String?): T? {
        if (raw.isNullOrBlank()) return null
        return try {
            json.decodeFromString<T>(raw)
        } catch (e: Exception) {
            logE("Failed to parse conflict response body", e)
            null
        }
    }

    /**
     * Sends [entity]'s current state to `PUT /api/sync/position/pair/{pairId}`, always attaching
     * this device's identity and the position's true local capture time (never "now" — see
     * [capturedAtIsoFromMillis]/[toCapturedAtIso]) so a stale offline replay is rejected by the
     * server instead of clobbering a newer write from another device.
     *
     * The Readium locator travels as a position hint keyed by this device, so
     * another device's precise position is never overwritten by ours — it used
     * to be a single shared `epub_locator` column (issue #102).
     */
    private suspend fun pushBookmark(pairId: Int, entity: BookmarkEntity, appendToLog: Boolean): PushOutcome {
        val response = api.updatePosition(
            "pair",
            pairId,
            PositionUpdateRequest(
                source = entity.source,
                epub_chapter = entity.epubChapter,
                epub_sentence_index = entity.epubSentenceIndex,
                sync_map_version = entity.syncMapVersion,
                audio_position_ms = entity.audioPositionMs,
                hint = entity.epubLocator?.let {
                    PositionHintDto(
                        kind = HINT_READIUM_LOCATOR,
                        value = it,
                        audio_position_ms = entity.locatorAudioMs,
                    )
                },
                append_to_log = appendToLog,
                device_id = deviceIdManager.deviceId,
                device_name = deviceIdManager.deviceName,
                captured_at = toCapturedAtIso(entity.capturedAt ?: entity.updatedAt),
            )
        )
        return when {
            response.isSuccessful -> {
                bookmarkDao.upsertBookmark(entity.copy(syncedToServer = true))
                PushOutcome.SYNCED
            }
            response.code() == 409 -> {
                val serverState = parseConflictBody<PositionResponse>(response.errorBody()?.string())
                if (serverState != null) {
                    bookmarkDao.upsertBookmark(serverState.toBookmarkEntity(scope, pairId, entity))
                    logW("pushBookmark pair=$pairId: 409 — this write lost, adopted server state")
                } else {
                    logE("pushBookmark pair=$pairId: 409 but could not parse server state")
                }
                PushOutcome.CONFLICT_ADOPTED
            }
            else -> {
                logW("pushBookmark pair=$pairId: failed — HTTP ${response.code()}")
                PushOutcome.FAILED
            }
        }
    }

    /**
     * Same contract as [pushBookmark] but scoped to a single medium —
     * `PUT /api/sync/position/{ebook|audiobook}/{mediaId}`.
     *
     * `source` is deliberately null: this is a progress-shaped write with no
     * claim about which format the user is in, and the server treats an
     * omitted `source` as "keep whatever is stored". Sending one here is how a
     * background audiobook save used to re-stamp `source=audiobook` mid-read.
     */
    private suspend fun pushProgress(mediaType: String, mediaId: Int, entity: UserProgressEntity): PushOutcome {
        val response = api.updatePosition(
            mediaType,
            mediaId,
            PositionUpdateRequest(
                source = null,
                epub_chapter = entity.epubChapter,
                epub_progress_percent = entity.epubProgressPercent,
                audio_position_ms = entity.audioPositionMs,
                is_completed = entity.isCompleted,
                device_id = entity.deviceId ?: deviceIdManager.deviceId,
                device_name = deviceIdManager.deviceName,
                captured_at = entity.capturedAt ?: capturedAtIsoFromMillis(entity.updatedAt),
            )
        )
        return when {
            response.isSuccessful -> {
                userProgressDao.upsertProgress(entity.copy(syncedToServer = true))
                PushOutcome.SYNCED
            }
            response.code() == 409 -> {
                val serverState = parseConflictBody<PositionResponse>(response.errorBody()?.string())
                if (serverState != null) {
                    userProgressDao.upsertProgress(
                        serverState.toProgressEntity(scope, mediaType, mediaId, entity))
                    logW("pushProgress $mediaType/$mediaId: 409 — this write lost, adopted server state")
                } else {
                    logE("pushProgress $mediaType/$mediaId: 409 but could not parse server state")
                }
                PushOutcome.CONFLICT_ADOPTED
            }
            else -> {
                logW("pushProgress $mediaType/$mediaId: failed — HTTP ${response.code()}")
                PushOutcome.FAILED
            }
        }
    }

    /**
     * Fetch the canonical position for a book, or null when the user has none.
     *
     * Returns null on 204 (never opened) *and* when the server is unreachable —
     * callers fall back to their local cache in both cases. The two are
     * distinguished by [PositionFetch.reachable] where it matters.
     */
    suspend fun fetchPosition(scope: String, id: Int): PositionFetch {
        return try {
            val response = api.getPosition(scope, id)
            when {
                response.code() == 204 -> PositionFetch(null, reachable = true)
                response.isSuccessful -> PositionFetch(response.body(), reachable = true)
                else -> {
                    logW("fetchPosition $scope/$id: HTTP ${response.code()}")
                    PositionFetch(null, reachable = false)
                }
            }
        } catch (e: CancellationException) {
            // A cancelled fetch is not "offline" — let the caller's scope see it.
            throw e
        } catch (e: Exception) {
            logW("fetchPosition $scope/$id: offline (${e.message})")
            PositionFetch(null, reachable = false)
        }
    }

    /**
     * Write a whole position in one request.
     *
     * Replaces the updateBookmark + updateProgress pair, which the server
     * adjudicated separately: either could be rejected while the other applied,
     * leaving two records that disagreed about where the reader was.
     */
    suspend fun updatePosition(
        scope: String,
        id: Int,
        request: PositionUpdateRequest,
    ): PositionResponse? {
        return try {
            val response = api.updatePosition(scope, id, request)
            when {
                response.isSuccessful -> response.body()
                response.code() == 409 -> {
                    val serverState = parseConflictBody<PositionResponse>(
                        response.errorBody()?.string())
                    logW("updatePosition $scope/$id: 409 — this write lost, adopting server state")
                    serverState
                }
                else -> {
                    logW("updatePosition $scope/$id: failed HTTP ${response.code()}")
                    null
                }
            }
        } catch (e: CancellationException) {
            // A cancelled save must propagate as cancellation, not be logged
            // as "offline" — the caller would otherwise continue into a Room
            // write on an already-cancelled coroutine (issue #164).
            throw e
        } catch (e: Exception) {
            logW("updatePosition $scope/$id: offline (${e.message})")
            null
        }
    }

    /**
     * Save an audio playback position for a paired book — one server write.
     *
     * The player used to write the bookmark and the progress row separately,
     * through the legacy endpoints. Two writes meant two staleness verdicts,
     * and a stop-write from a backgrounded player could land after the
     * reader's save and stamp `source = audiobook` over it, which is what sent
     * the app to the player when the user had last been reading. The derived
     * progress row is now written server-side from this same call.
     *
     * [claimFormat] decides whether this save is allowed to claim "audiobook"
     * as the format `resolvePairOpenTarget` routes to next. Product rule: only
     * a save that happens while the player is actively playing, or one
     * triggered by an explicit user playback command (play/pause tap, seek,
     * skip, sleep-timer stop, Android Auto MediaSession command), may claim
     * it. A save from a paused/idle player still needs to persist the
     * position — that's what this call does regardless — but must leave
     * `source` alone: omitted from the wire request (the server keeps
     * whatever is stored, see `PositionUpdateRequest.source`) and untouched in
     * the local Room bookmark (see `updateBookmark`'s `stampSource`). This is
     * the fix for background saves (heartbeats, teardown while paused)
     * hijacking routing back to the player after the user switched to
     * reading.
     *
     * Exception (issue #61/#40 fix 4): when there is no existing local
     * bookmark row at all, `claimFormat=false` is escalated to a claim
     * anyway. A first-ever write has no previously-stored `source` to
     * preserve — omitting it would leave the server's new-row default
     * (`ebook`) standing while this device's local fallback stamps
     * `"audiobook"`, permanently disagreeing about routing from the very
     * first write. The user did just play this book, so it's correct for
     * this one write to claim it.
     */
    suspend fun savePlaybackPosition(
        pairId: Int,
        audioPositionMs: Int,
        appendToLog: Boolean = false,
        claimFormat: Boolean = true,
        // False = a throttled heartbeat (issue #65): write Room only and leave
        // the row unsynced; the next eligible tick or boundary save pushes it.
        // Returns whether the canonical server write landed.
        pushToServer: Boolean = true,
    ): Boolean {
        val hasExistingRow = bookmarkDao.getBookmark(scope, pairId) != null
        val effectiveClaim = claimFormat || !hasExistingRow

        // Room-first (issue #164, contract § "The write gate"): the local row
        // must land even if the coroutine is cancelled inside the network call
        // below (paused swipe-away, offline pause burning the connect
        // timeout). Written unsynced; flipped to synced only when the PUT
        // actually succeeds, so a crash in between leaves a row the startup
        // reconcile / WorkManager sweep still delivers.
        updateBookmark(
            pairId = pairId,
            source = "audiobook",
            audioPositionMs = audioPositionMs,
            appendToLog = false,
            pushToServer = false,
            stampSource = effectiveClaim,
            markSynced = false,
        )
        if (!pushToServer) return false

        val result = updatePosition(
            "pair", pairId,
            PositionUpdateRequest(
                source = if (effectiveClaim) "audiobook" else null,
                audio_position_ms = audioPositionMs,
                append_to_log = appendToLog,
                captured_at = capturedAtIsoFromMillis(System.currentTimeMillis()),
                device_id = deviceId,
                device_name = deviceName,
            ),
        )
        if (result == null) {
            // Canonical write failed — re-run the legacy push (which queues
            // pending_sync on failure) and mirror the history entry locally,
            // exactly as the pre-Room-first code did on this path.
            updateBookmark(
                pairId = pairId,
                source = "audiobook",
                audioPositionMs = audioPositionMs,
                appendToLog = appendToLog,
                pushToServer = true,
                stampSource = effectiveClaim,
            )
        } else {
            bookmarkDao.markSynced(scope, pairId)
        }
        return result != null
    }

    /** Same, for a standalone audiobook (no pair). See [savePlaybackPosition]'s
     *  [claimFormat] doc — standalone audio has no paired ebook to route
     *  between, so this only affects what's sent on the wire. */
    suspend fun savePlaybackPositionStandalone(
        audiobookId: Int,
        audioPositionMs: Int,
        claimFormat: Boolean = true,
        pushToServer: Boolean = true,
    ): Boolean {
        // Room-first, mirroring savePlaybackPosition above (issue #164).
        updateProgress(
            mediaType = "audiobook",
            mediaId = audiobookId,
            audioPositionMs = audioPositionMs,
            pushToServer = false,
            markSynced = false,
        )
        if (!pushToServer) return false

        val result = updatePosition(
            "audiobook", audiobookId,
            PositionUpdateRequest(
                source = if (claimFormat) "audiobook" else null,
                audio_position_ms = audioPositionMs,
                captured_at = capturedAtIsoFromMillis(System.currentTimeMillis()),
                device_id = deviceId,
                device_name = deviceName,
            ),
        )
        if (result == null) {
            // Failed canonical write — the legacy push path leaves the row
            // unsynced for the sweep when it fails too.
            updateProgress(
                mediaType = "audiobook",
                mediaId = audiobookId,
                audioPositionMs = audioPositionMs,
                pushToServer = true,
            )
        } else {
            userProgressDao.markSynced(scope, "audiobook", audiobookId)
        }
        return result != null
    }

    /**
     * Boundary-save variant of [savePlaybackPosition] that survives the
     * caller's teardown (issue #164): pause, STATE_ENDED, cast switch,
     * controller disconnect, service onDestroy and PlayerViewModel.onCleared
     * all fire at moments where the launching scope is about to be cancelled.
     * Runs on [appScope] under [NonCancellable], the same shape as
     * [saveReaderPosition]. The throttled heartbeat deliberately does NOT use
     * this — a lost heartbeat is recovered by the next tick.
     *
     * Returns the [Job] so tests can await completion; callers fire and forget.
     */
    fun savePlaybackPositionDetached(
        pairId: Int,
        audioPositionMs: Int,
        appendToLog: Boolean = false,
        claimFormat: Boolean = true,
    ): Job = appScope.launch {
        withContext(NonCancellable) {
            savePlaybackPosition(
                pairId = pairId,
                audioPositionMs = audioPositionMs,
                appendToLog = appendToLog,
                claimFormat = claimFormat,
            )
        }
    }

    /** Same, for a standalone audiobook. See [savePlaybackPositionDetached]. */
    fun savePlaybackPositionStandaloneDetached(
        audiobookId: Int,
        audioPositionMs: Int,
        claimFormat: Boolean = true,
    ): Job = appScope.launch {
        withContext(NonCancellable) {
            savePlaybackPositionStandalone(
                audiobookId = audiobookId,
                audioPositionMs = audioPositionMs,
                claimFormat = claimFormat,
            )
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
        // The sync-map version [epubSentenceIndex] was resolved against (issue
        // #116). Only consulted when a sentence index is passed; otherwise the
        // merged row keeps the version belonging to the index it keeps.
        syncMapVersion: Int? = null,
        audioPositionMs: Int? = null,
        epubLocator: String? = null,
        // Audio position the epubLocator corresponds to; enables exact locator
        // reuse when returning from the player with little audio movement.
        locatorAudioMs: Int? = null,
        // When false (heartbeat saves every 5s), only the Bookmark row is updated
        // and no BookmarkLog row is written — server and local history stay clean.
        // Set true on pause / stop / 30-min-continuous-playback boundaries.
        appendToLog: Boolean = false,
        // When false, write the Room row only. Callers that have already sent
        // the position via updatePosition use this to keep the offline cache
        // warm without issuing a second server write — two writes per save is
        // the very thing the canonical endpoint exists to remove.
        pushToServer: Boolean = true,
        // When false, [source] is NOT written — the merged row (and every
        // downstream use of it: the BookmarkLog entry, the retry push, the
        // pending_sync row) keeps whatever
        // `source` is already stored, only falling back to [source] when no
        // bookmark exists yet at all. Backs `savePlaybackPosition`'s
        // `claimFormat=false` path: the position still needs to move, but a
        // save from a paused/idle player must not re-route which format opens
        // next (issue: background saves hijacking format routing).
        stampSource: Boolean = true,
        // Only meaningful with pushToServer=false. True (the default) marks the
        // local-only row synced because the caller already pushed it; false
        // leaves it UNSYNCED — a throttled heartbeat (issue #65) that has not
        // reached the server, which the startup reconcile and the WorkManager
        // sweep must still deliver if the app dies before the next push.
        markSynced: Boolean = true,
    ) {
        val existing = bookmarkDao.getBookmark(scope, pairId)
        val nowMillis = System.currentTimeMillis()
        val resolvedSource = if (stampSource) source else (existing?.source ?: source)

        // Merge with existing
        val merged = BookmarkEntity(
            bookPairId = pairId,
            source = resolvedSource,
            epubChapter = epubChapter ?: existing?.epubChapter,
            epubSentenceIndex = epubSentenceIndex ?: existing?.epubSentenceIndex,
            // The version travels with the index it describes.
            syncMapVersion = if (epubSentenceIndex != null) syncMapVersion else existing?.syncMapVersion,
            audioPositionMs = audioPositionMs ?: existing?.audioPositionMs,
            epubLocator = epubLocator ?: existing?.epubLocator,
            locatorAudioMs = locatorAudioMs
                ?: (if (epubLocator != null) audioPositionMs ?: existing?.locatorAudioMs
                    else existing?.locatorAudioMs),
            updatedAt = nowMillis.toString(),
            // The moment this position was actually captured on-device — sent as
            // `captured_at` on sync so a later stale replay of this same write can be
            // told apart from a fresh one (issue #54).
            capturedAt = capturedAtIsoFromMillis(nowMillis),
            deviceId = deviceIdManager.deviceId,
            deviceName = deviceIdManager.deviceName,
                scopeKey = scope,
            syncedToServer = false,
        )

        // Save locally. A local-only write is normally marked synced because
        // the caller has already sent this position through the canonical
        // endpoint — leaving it unsynced would have the startup reconcile push
        // it a second time. markSynced=false is the exception (see above).
        bookmarkDao.upsertBookmark(
            if (pushToServer || !markSynced) merged else merged.copy(syncedToServer = true))

        if (!pushToServer) return

        // Record a local history entry ONLY on meaningful session boundaries so the
        // offline history mirrors what the server will record. Heartbeat saves stay
        // out of the log on both sides.
        if (appendToLog) {
            bookmarkLogDao.insertLocal(
                BookmarkLogEntity(
                    serverId = null,
                    bookPairId = pairId,
                    source = resolvedSource,
                    prevEpubChapter = existing?.epubChapter,
                    prevEpubSentenceIndex = existing?.epubSentenceIndex,
                    prevAudioPositionMs = existing?.audioPositionMs,
                    newEpubChapter = merged.epubChapter,
                    newEpubSentenceIndex = merged.epubSentenceIndex,
                    newAudioPositionMs = merged.audioPositionMs,
                    changedAt = localHistoryTimestamp(nowMillis),
                    deviceId = deviceIdManager.deviceId,
                        scopeKey = scope,
                    deviceName = deviceIdManager.deviceName,
                )
            )
        }

        // Try immediate sync
        try {
            val outcome = pushBookmark(pairId, merged, appendToLog)
            if (outcome == PushOutcome.FAILED) {
                logW("updateBookmark sync failed — queuing for later")
                pendingSyncDao.insert(
                    PendingSyncEntity(
                        // Stamp the owner so the drain cannot replay this into
                        // someone else's account (issue #314).
                        scopeKey = scope,
                        bookPairId = pairId,
                        source = resolvedSource,
                        epubChapter = merged.epubChapter,
                        epubSentenceIndex = merged.epubSentenceIndex,
                        syncMapVersion = merged.syncMapVersion,
                        audioPositionMs = merged.audioPositionMs,
                        epubLocator = merged.epubLocator,
                        locatorAudioMs = merged.locatorAudioMs,
                        appendToLog = appendToLog,
                        // Preserve the true capture moment (not whenever this queue
                        // insert happens to run) so a later replay's captured_at is
                        // still accurate — see processPendingSync.
                        createdAt = nowMillis,
                    )
                )
            }
            // SYNCED and CONFLICT_ADOPTED both mean "don't queue" — pushBookmark already
            // wrote the correct local state (ours, or the server's if we lost a 409) either way.
        } catch (e: Exception) {
            logW("updateBookmark sync failed — queuing for later (${e.message})")
            pendingSyncDao.insert(
                PendingSyncEntity(
                    scopeKey = scope,
                    bookPairId = pairId,
                    source = resolvedSource,
                    epubChapter = merged.epubChapter,
                    epubSentenceIndex = merged.epubSentenceIndex,
                    syncMapVersion = merged.syncMapVersion,
                    audioPositionMs = merged.audioPositionMs,
                    epubLocator = merged.epubLocator,
                    locatorAudioMs = merged.locatorAudioMs,
                    appendToLog = appendToLog,
                    createdAt = nowMillis,
                )
            )
        }
    }

    /**
     * Executes an already-captured reader-position save: writes the local
     * Room row FIRST, then attempts the canonical position PUT, falling
     * through to the legacy pending-sync queue on failure. See
     * [ReaderPositionSnapshot] — the caller (`ReaderActivity.savePosition`)
     * must capture everything this needs while the activity/publication/
     * navigator are still alive, since this runs on [appScope] and must not
     * touch any of them.
     *
     * Runs under [NonCancellable] on a scope that is never a child of the
     * caller's, so cancelling the caller (the activity's `lifecycleScope`
     * being torn down) cannot abort it mid-write — this is the fix for issue
     * #61/#40's close-flush bug: the old code ran Room write + server call
     * inside `lifecycleScope.launch`, Room write LAST, so a back-press ->
     * onPause -> finish() sequence killed the coroutine mid network call and
     * lost both writes.
     *
     * The sync-point lookup (chapter+text -> sentence+audio position) also
     * happens in here rather than in the caller (fix 2): it's only a Room
     * read (see [getSyncPointForEpubText]), so — like the rest of this save —
     * it belongs on the side that can't be cancelled by activity teardown,
     * not on the caller's `lifecycleScope`.
     *
     * Returns the [Job] so tests can await completion; `ReaderActivity` fires
     * and forgets it.
     */
    fun saveReaderPosition(snapshot: ReaderPositionSnapshot): Job = appScope.launch {
        withContext(NonCancellable) {
            val capturedAtIso = capturedAtIsoFromMillis(snapshot.capturedAtMillis)
            val existing = bookmarkDao.getBookmark(scope, snapshot.pairId)

            // A sync-map match upgrades the coarse spine chapter to a precise
            // sentence + audio position. A miss is not a failure: the chapter
            // and the preview still describe where the reader is, and other
            // clients can resolve from them. [ReaderPositionSnapshot.skipSyncPointLookup]
            // is set when a manual audio-sync write (syncSelectedTextToAudio)
            // is already in flight for this page — resolving a match here too
            // could overwrite that fresher, deliberately-chosen audio
            // position with a stale automatic guess.
            val syncPoint = if (snapshot.skipSyncPointLookup) null
                else resolveSyncPoint(snapshot.pairId, snapshot.chapterIndex, snapshot.textPreview)
            val resolvedChapterIndex = syncPoint?.epubChapter ?: snapshot.chapterIndex
            val resolvedSentenceIndex = syncPoint?.epubSentenceIndex
            val resolvedAudioMs = syncPoint?.audioStartMs
            // Which map that sentence index is a coordinate of (issue #116):
            // the version the cached points came from when a match was found;
            // otherwise the merged row reuses the existing index, so it keeps
            // the existing version too. Read here, at resolution time, so a
            // queued replay attests what was true now, not at push time.
            val resolvedSyncMapVersion =
                if (syncPoint != null) bookPairDao.getPairById(snapshot.pairId)?.syncMapVersion
                else existing?.syncMapVersion

            // Room-first: this is the one write that must land no matter what
            // happens to the network call below. Written unsynced, then
            // flipped to synced only once the PUT below actually succeeds
            // (fix 5) — otherwise a failed PUT would leave a row claiming to
            // be synced when the server never saw it.
            val merged = BookmarkEntity(
                bookPairId = snapshot.pairId,
                source = "ebook",
                epubChapter = resolvedChapterIndex,
                epubSentenceIndex = resolvedSentenceIndex ?: existing?.epubSentenceIndex,
                syncMapVersion = resolvedSyncMapVersion,
                audioPositionMs = resolvedAudioMs ?: existing?.audioPositionMs,
                epubLocator = snapshot.locatorJson,
                locatorAudioMs = resolvedAudioMs ?: existing?.locatorAudioMs,
                updatedAt = snapshot.capturedAtMillis.toString(),
                capturedAt = capturedAtIso,
                deviceId = deviceId,
                deviceName = deviceName,
                    scopeKey = scope,
                syncedToServer = false,
            )
            bookmarkDao.upsertBookmark(merged)

            val request = PositionUpdateRequest(
                source = "ebook",
                epub_chapter = resolvedChapterIndex,
                epub_sentence_index = resolvedSentenceIndex,
                sync_map_version = if (resolvedSentenceIndex != null) resolvedSyncMapVersion else null,
                epub_text_preview = snapshot.textPreview.takeIf { it.isNotEmpty() },
                // Null when it can't be computed; omitted rather than sent as
                // 0, which would overwrite a real percent.
                epub_progress_percent = snapshot.progressPercent,
                audio_position_ms = resolvedAudioMs,
                hint = PositionHintDto(
                    kind = HINT_READIUM_LOCATOR,
                    value = snapshot.locatorJson,
                    audio_position_ms = resolvedAudioMs,
                ),
                captured_at = capturedAtIso,
                device_id = deviceId,
                device_name = deviceName,
            )
            val result = updatePosition("pair", snapshot.pairId, request)
            if (result == null) {
                // Canonical write failed (offline / server error) — queue for
                // retry with the TRUE capture moment, not whenever this
                // fallback happens to run, so a later replay isn't mistaken
                // for a fresh write (issue #54).
                pendingSyncDao.insert(
                    PendingSyncEntity(
                        scopeKey = scope,
                        bookPairId = snapshot.pairId,
                        source = "ebook",
                        epubChapter = merged.epubChapter,
                        epubSentenceIndex = merged.epubSentenceIndex,
                        syncMapVersion = merged.syncMapVersion,
                        audioPositionMs = merged.audioPositionMs,
                        epubLocator = merged.epubLocator,
                        locatorAudioMs = merged.locatorAudioMs,
                        appendToLog = false,
                        createdAt = snapshot.capturedAtMillis,
                    )
                )
            } else {
                // The canonical write landed — this row now genuinely matches
                // the server, so it's safe to mark synced (fix 5).
                bookmarkDao.markSynced(scope, snapshot.pairId)
            }
        }
    }

    /**
     * Stamps only the local bookmark's `source` — never touches
     * chapter/sentence/locator/audio, never calls the server, never enqueues
     * `pending_sync`.
     *
     * Backs [com.booksync.ui.reader.PositionSavePolicy]'s `LocalMetadataOnly`
     * verdict: while a restore is unresolved the reader view sits at spine 0,
     * and writing that into the real anchors would recreate the chapter-0
     * data loss this whole redesign exists to fix. But `resolvePairOpenTarget`
     * keys off this row's `source`, so *something* has to record "the user
     * was last in the reader" even when the position itself can't be trusted
     * yet — this is that something.
     *
     * Deliberately does NOT bump `updatedAt`/`capturedAt` on an existing row
     * (issue #61/#40 fix 5): doing so used to make `refreshBookmark` think
     * this local row was newer than it really is, which blocks pulling a
     * genuinely newer position from another device on the next open. Only a
     * brand-new row (no existing bookmark at all) gets a fresh timestamp,
     * since there's nothing to preserve.
     */
    suspend fun updateBookmarkMetadata(pairId: Int, source: String) {
        val existing = bookmarkDao.getBookmark(scope, pairId)
        val nowMillis = System.currentTimeMillis()
        val base = existing ?: BookmarkEntity(
            bookPairId = pairId,
            source = source,
            epubChapter = null,
            epubSentenceIndex = null,
            audioPositionMs = null,
            updatedAt = nowMillis.toString(),
                scopeKey = scope,
            capturedAt = capturedAtIsoFromMillis(nowMillis),
        )
        val merged = base.copy(
            source = source,
            // Marked synced so this never gets picked up by the
            // syncedToServer=false startup retry sweep — there is nothing to
            // retry, this write was never meant to reach the server.
            syncedToServer = true,
        )
        bookmarkDao.upsertBookmark(merged)
    }

    /**
     * Update just the EPUB locator JSON for a bookmark (used by Readium reader).
     * When [audioMs] is provided, it records the audio position this locator
     * corresponds to, enabling exact locator reuse on the next reader open.
     */
    suspend fun updateBookmarkLocator(pairId: Int, locatorJson: String, audioMs: Int? = null) {
        if (audioMs != null) {
            bookmarkDao.updateLocatorWithAudio(scope, pairId, locatorJson, audioMs)
        } else {
            bookmarkDao.updateLocator(scope, pairId, locatorJson)
        }
    }

    // ============ Position Conversion ============

    /**
     * Find the best matching SyncPointEntity for a given extracted EPUB text snippet.
     *
     * The algorithm itself lives in [SyncMatcher] — it is shared, vector-for-vector,
     * with the server's services/sync_matcher.py (issue #41), so the same page yields
     * the same audio position on Android and in the web reader. This wrapper only does
     * the I/O (DAO read) and logging that the pure matcher deliberately can't.
     *
     * Dispatched onto [Dispatchers.Default] for callers on the main thread.
     * [saveReaderPosition] calls [resolveSyncPoint] directly instead — it
     * already runs off-main on [appScope], and hopping through another
     * dispatcher there would break the "Room write happens before anything
     * else, deterministically" guarantee that's the whole point of fix 2.
     */
    suspend fun getSyncPointForEpubText(pairId: Int, chapter: Int, epubText: String): SyncPointEntity? =
        withContext(Dispatchers.Default) { resolveSyncPoint(pairId, chapter, epubText) }

    private suspend fun resolveSyncPoint(pairId: Int, chapter: Int, epubText: String): SyncPointEntity? {
        val allPoints = syncPointDao.getPointsForPair(pairId)

        if (allPoints.isEmpty()) {
            android.util.Log.d("SyncMatch", "No sync points found for pair $pairId")
            return null
        }

        android.util.Log.d("SyncMatch", "Available sync chapters: ${allPoints.map { it.epubChapter }.distinct().sorted()}")

        val matchedPoint = SyncMatcher.match(allPoints, epubText, chapter)
        if (matchedPoint == null) {
            android.util.Log.d("SyncMatch", "No exact or fuzzy match in any chapter (searched ±10 around chapter $chapter)")
        } else {
            android.util.Log.d("SyncMatch", "MATCH in chapter ${matchedPoint.epubChapter}! " +
                "sentence=${matchedPoint.epubSentenceIndex} audio=${matchedPoint.audioStartMs}ms " +
                "preview='${matchedPoint.epubTextPreview?.take(60)}'")
        }
        return matchedPoint
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
     *
     * The default lands you just *before* the sentence you were reading rather
     * than on top of it — the same offset a resume uses, so "switch to listening"
     * and "unpause" feel alike. Callers used to pass a literal 2000 here while
     * this default said 10_000 (issue #42).
     */
    suspend fun epubToAudioText(
        pairId: Int,
        chapter: Int,
        epubText: String,
        rewindMs: Int = PlaybackOffsets.RESUME_REWIND_MS.toInt(),
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

    /**
     * Text preview stored for a chapter + sentence-index anchor, or "" if the
     * pair has no sync map or no such point.
     *
     * The chapter/sentence pair is the portable cross-device position anchor
     * (issue #40); this turns it back into searchable text so the reader can
     * resolve a real page position when a device-local locator can't be
     * trusted. Falls back to the nearest earlier sentence in the same chapter,
     * matching how the server's `_convert_position` resolves a preview.
     */
    suspend fun epubTextForSentence(pairId: Int, chapter: Int, sentenceIndex: Int?): String {
        val inChapter = syncPointDao.getPointsForPair(pairId)
            .filter { it.epubChapter == chapter }
            .sortedBy { it.epubSentenceIndex }
        if (inChapter.isEmpty()) return ""
        val target = sentenceIndex ?: 0
        val best = inChapter.lastOrNull { it.epubSentenceIndex <= target && !it.epubTextPreview.isNullOrBlank() }
            ?: inChapter.firstOrNull { !it.epubTextPreview.isNullOrBlank() }
        return best?.epubTextPreview ?: ""
    }

    // ============ User Progress ============

    /** Get the user progress flow from local cache */
    fun getProgressFlow(mediaType: String, mediaId: Int): Flow<UserProgressEntity?> =
        userProgressDao.getProgressFlow(scope, mediaType, mediaId)

    /**
     * Refresh progress from the server's canonical record for this medium,
     * respecting unsynced and newer local data — the same decision rules as
     * [refreshBookmark] (issue #162). A 204 (never opened) and an unreachable
     * server both come back null: nothing to pull, the local cache stands.
     */
    suspend fun refreshProgress(mediaType: String, mediaId: Int) {
        val remote = fetchPosition(mediaType, mediaId).position ?: return
        val existing = userProgressDao.getProgress(scope, mediaType, mediaId)

        // Never overwrite unsynced local data — offline progress must survive.
        if (existing != null && !existing.syncedToServer) {
            log("refreshProgress $mediaType/$mediaId: local has unsynced changes, skipping server pull")
            return
        }

        // Only pull if the server record is at least as new as the local one.
        // Compare capture moments (falling back to updatedAt), the same axis
        // syncAllBookmarksAndProgress adjudicates with.
        val remoteTs = parseSyncTimestamp(preferCapturedAt(remote.captured_at, remote.updated_at))
        val localTs = existing?.let { it.capturedAt?.let { c -> parseSyncTimestamp(c) } ?: it.updatedAt } ?: 0L

        if (existing == null || remoteTs >= localTs) {
            userProgressDao.upsertProgress(
                // `updatedAt` is deliberately local wall-clock rather than the
                // remote string: it only orders the local Continue list.
                remote.toProgressEntity(scope, mediaType, mediaId, existing)
                    .copy(updatedAt = System.currentTimeMillis())
            )
        } else {
            log("refreshProgress $mediaType/$mediaId: local is newer (local=$localTs, remote=$remoteTs), keeping local")
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
        deviceId: String? = deviceIdManager.deviceId,
        // See updateBookmark: false writes the Room row only, for callers that
        // have already sent this position through the canonical endpoint.
        pushToServer: Boolean = true,
        // See updateBookmark.markSynced — false keeps a local-only write
        // unsynced so the sweep delivers it (throttled heartbeat, issue #65).
        markSynced: Boolean = true,
    ) {
        val existing = userProgressDao.getProgress(scope, mediaType, mediaId)
        val nowMillis = System.currentTimeMillis()

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
            updatedAt = nowMillis,
            deviceId = deviceId,
            deviceName = deviceIdManager.deviceName,
            // The moment this position was actually captured on-device — see
            // BookmarkEntity.capturedAt / pushProgress for why this isn't "now" at sync time.
            capturedAt = capturedAtIsoFromMillis(nowMillis),
                scopeKey = scope,
            syncedToServer = false
        )

        userProgressDao.upsertProgress(
            if (pushToServer || !markSynced) merged else merged.copy(syncedToServer = true))

        if (!pushToServer) return

        try {
            pushProgress(mediaType, mediaId, merged)
            // SYNCED and CONFLICT_ADOPTED both mean the local row is now settled (either
            // marked synced, or overwritten with the server's authoritative state on a
            // 409 loss) — pushProgress already wrote it either way.
        } catch (_: Exception) {
            // Keep syncedToServer = false, will be picked up by SyncWorker
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
                // Null covers both "never opened" (204) and "unreachable" — an
                // unsynced local row is still worth pushing in the first case,
                // and the push simply fails in the second, so both are handled
                // by falling through to the local-wins branch.
                val remote = fetchPosition("pair", pair.id).position
                val local = bookmarkDao.getBookmark(scope, pair.id)
                // Prefer captured_at (the true on-device capture moment) over updated_at
                // (a bookkeeping timestamp) whenever the server/local row provides one —
                // now that the server never re-stamps a rejected stale write with "now",
                // this comparison is reliable (issue #54).
                val remoteTs = if (remote != null)
                    parseSyncTimestamp(preferCapturedAt(remote.captured_at, remote.updated_at)) else 0L
                val localTs  = if (local != null) parseSyncTimestamp(preferCapturedAt(local.capturedAt, local.updatedAt)) else 0L

                when {
                    // Unsynced local data always wins — push to server. A 409 here means
                    // another device's write is actually newer; pushBookmark adopts it.
                    local != null && !local.syncedToServer -> {
                        pushBookmark(pair.id, local, appendToLog = false)
                        log("syncBookmark pair=${pair.id}: pushed unsynced local")
                    }
                    remote == null -> { /* nothing on the server, nothing unsynced here */ }
                    local == null || remoteTs > localTs -> {
                        bookmarkDao.upsertBookmark(remote.toBookmarkEntity(scope, pair.id, local))
                        log("syncBookmark pair=${pair.id}: pulled from server ts=$remoteTs")
                    }
                    localTs > remoteTs -> {
                        pushBookmark(pair.id, local, appendToLog = false)
                        log("syncBookmark pair=${pair.id}: pushed local ts=$localTs")
                    }
                }
            } catch (_: Exception) { /* offline or no server record yet — skip */ }

            // --- Audiobook progress ---
            try {
                val remote = fetchPosition("audiobook", pair.audiobookId).position
                val local  = userProgressDao.getProgress(scope, "audiobook", pair.audiobookId)
                val remoteTs = if (remote != null)
                    parseSyncTimestamp(preferCapturedAt(remote.captured_at, remote.updated_at)) else 0L
                val localTs  = if (local != null) {
                    local.capturedAt?.let { parseSyncTimestamp(it) } ?: local.updatedAt
                } else 0L

                when {
                    // Unsynced local data always wins — push to server
                    local != null && !local.syncedToServer -> {
                        pushProgress("audiobook", pair.audiobookId, local)
                        log("syncProgress audiobook=${pair.audiobookId}: pushed unsynced local")
                    }
                    remote == null -> { /* nothing on the server, nothing unsynced here */ }
                    local == null || remoteTs > localTs -> {
                        userProgressDao.upsertProgress(
                            remote.toProgressEntity(scope, "audiobook", pair.audiobookId, local))
                        log("syncProgress audiobook=${pair.audiobookId}: pulled from server ts=$remoteTs")
                    }
                    localTs > remoteTs -> {
                        pushProgress("audiobook", pair.audiobookId, local)
                        log("syncProgress audiobook=${pair.audiobookId}: pushed local ts=$localTs")
                    }
                }
            } catch (_: Exception) { /* offline or no server record yet — skip */ }
        }
    }

    // ============ Offline Sync ============

    /**
     * Process pending sync queue — called by WorkManager.
     *
     * Critical fix for issue #54's plane-replay regression: each queued item sends its
     * ORIGINAL local capture time as `captured_at` (never "now" — see
     * [PendingSyncEntity.createdAt], set at the moment the write was first attempted, and
     * [UserProgressEntity.updatedAt]). A device that was offline for days and replays a
     * stale queue therefore gets rejected with 409 instead of the server treating the
     * replay as a fresh, most-recent write. On 409 the item is dropped (it lost — no
     * retry) and the server's authoritative state is adopted locally.
     */
    suspend fun processPendingSync() = pendingSyncMutex.withLock {
        // Every push below is scoped (issue #314). Before this, all three loops
        // read "whatever is unsynced" and sent it under whatever token happened to
        // be stored, so one account's positions committed into another's.
        val scope = scopeKeyOrNull
        if (scope == null) {
            // Holding the queue costs a delay; guessing costs someone else's
            // reading position.
            logW("processPendingSync — no resolvable account; leaving the queue untouched")
            return@withLock
        }
        val pendingBookmarks = pendingSyncDao.getPendingForScope(scope)
        if (pendingBookmarks.isNotEmpty()) log("processPendingSync — ${pendingBookmarks.size} pending bookmarks")
        for (sync in pendingBookmarks) {
            try {
                val response = api.updatePosition(
                    "pair",
                    sync.bookPairId,
                    PositionUpdateRequest(
                        source = sync.source,
                        epub_chapter = sync.epubChapter,
                        epub_sentence_index = sync.epubSentenceIndex,
                        // The version that was true when the index was
                        // resolved — not whatever the cache holds now.
                        sync_map_version = sync.syncMapVersion,
                        audio_position_ms = sync.audioPositionMs,
                        hint = sync.epubLocator?.let {
                            PositionHintDto(
                                kind = HINT_READIUM_LOCATOR,
                                value = it,
                                audio_position_ms = sync.locatorAudioMs,
                            )
                        },
                        append_to_log = sync.appendToLog,
                        device_id = deviceIdManager.deviceId,
                        device_name = deviceIdManager.deviceName,
                        captured_at = capturedAtIsoFromMillis(sync.createdAt),
                    )
                )
                when {
                    response.isSuccessful -> {
                        pendingSyncDao.delete(sync)
                        log("processPendingSync — bookmark pairId=${sync.bookPairId} synced")
                    }
                    response.code() == 409 -> {
                        val serverState = parseConflictBody<PositionResponse>(response.errorBody()?.string())
                        if (serverState != null) {
                            bookmarkDao.upsertBookmark(serverState.toBookmarkEntity(scope, 
                                sync.bookPairId, bookmarkDao.getBookmark(scope, sync.bookPairId)))
                        }
                        pendingSyncDao.delete(sync)
                        logW("processPendingSync — bookmark pairId=${sync.bookPairId} lost to a newer write (409) — dropped, adopted server state")
                    }
                    else -> {
                        logW("processPendingSync — bookmark pairId=${sync.bookPairId} failed HTTP ${response.code()}, stopping")
                        break
                    }
                }
            } catch (e: Exception) {
                logW("processPendingSync — still offline, stopping (${e.message})")
                break
            }
        }

        // Bookmark rows left unsynced by a throttled heartbeat (issue #65) or a
        // PUT that failed before it could be queued. pushBookmark sends the
        // row's own captured_at, so a stale replay still loses to a newer
        // write on another device (409 → adopt server state).
        for (row in bookmarkDao.getUnsyncedBookmarks(scope)) {
            try {
                if (pushBookmark(row.bookPairId, row, appendToLog = false) == PushOutcome.FAILED) {
                    logW("processPendingSync — unsynced bookmark pairId=${row.bookPairId} failed, stopping")
                    break
                }
            } catch (e: Exception) {
                logW("processPendingSync — still offline, stopping (${e.message})")
                break
            }
        }

        // Process Progress
        val unsyncedProgress = userProgressDao.getUnsyncedProgress(scope)
        for (prog in unsyncedProgress) {
             try {
                 val outcome = pushProgress(prog.mediaType, prog.mediaId, prog)
                 if (outcome == PushOutcome.FAILED) {
                     logW("processPendingSync — progress ${prog.mediaType}/${prog.mediaId} failed, stopping")
                     break
                 }
                 // SYNCED and CONFLICT_ADOPTED both mean this row is settled — pushProgress
                 // already wrote it (marked synced, or overwritten with the server's
                 // authoritative state on a 409 loss).
             } catch (_: Exception) {
                 break
             }
        }
    }

    // ============ New Items (unacknowledged) ============

    fun getNewEbooksFlow(): Flow<List<EBookEntity>> = acknowledgedItemDao.getNewEbooks(scope)
    fun getNewAudiobooksFlow(): Flow<List<AudioBookEntity>> = acknowledgedItemDao.getNewAudiobooks(scope)
    fun getNewPairsFlow(): Flow<List<BookPairEntity>> = acknowledgedItemDao.getNewPairs(scope)
    fun getNewEbookCountFlow(): Flow<Int> = acknowledgedItemDao.getNewEbookCount(scope)
    fun getNewAudiobookCountFlow(): Flow<Int> = acknowledgedItemDao.getNewAudiobookCount(scope)
    fun getNewPairCountFlow(): Flow<Int> = acknowledgedItemDao.getNewPairCount(scope)

    suspend fun acknowledgeItems(ids: List<Int>, type: String) {
        acknowledgedItemDao.acknowledge(ids.map { AcknowledgedItemEntity(scope, it, type) })
    }
}

// ============ Timestamp helpers (issue #54 conflict resolution) ============
//
// Top-level (not private/member) so they're pure and unit-testable without constructing a
// BookSyncRepository — see SyncConflictHelpersTest. `internal` visibility keeps them out of
// the public API surface while still reachable from the test source set.

/**
 * Parses a timestamp that is either epoch-millis (local format, e.g. [BookmarkEntity.updatedAt]
 * when set on-device) or ISO-8601 (server format) into epoch millis. Returns 0L when [ts] is
 * null/blank/unparseable.
 */
internal fun parseSyncTimestamp(ts: String?): Long {
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

/** Epoch millis -> canonical ISO-8601 (`Instant.toString()`, always `Z`-suffixed UTC). */
internal fun capturedAtIsoFromMillis(millis: Long): String =
    java.time.Instant.ofEpochMilli(millis).toString()

/**
 * Converts a local capture timestamp — either the epoch-millis string a [BookmarkEntity]
 * holds when set on-device, or an ISO-8601 string when it was last pulled from the server —
 * into the ISO-8601 string the server's `captured_at` field expects. Returns null when [ts]
 * is null/blank/unparseable so callers omit `captured_at` entirely rather than send a bogus
 * instant (the server treats a missing `captured_at` as legacy last-write-wins and never
 * rejects the write).
 */
internal fun toCapturedAtIso(ts: String?): String? {
    val millis = parseSyncTimestamp(ts)
    if (millis <= 0L) return null
    return capturedAtIsoFromMillis(millis)
}

/**
 * Picks the timestamp to use for last-write-wins comparisons: prefer `captured_at` (the true
 * moment a position was recorded on the writing device) over `updated_at` (a row-modified
 * bookkeeping timestamp) whenever one is present. Falls back to [updatedAt] otherwise —
 * always the case for servers/rows predating issue #54's conflict-resolution contract.
 */
internal fun preferCapturedAt(capturedAt: String?, updatedAt: String): String =
    if (!capturedAt.isNullOrBlank()) capturedAt else updatedAt

// ============ Position response -> entity mappers ============

/**
 * The Readium locator this response currently vouches for, if any.
 *
 * Only a hint tagged `current` counts: a stale one describes a page the anchor
 * has since moved away from. It is deliberately not *deleted* server-side —
 * its own device makes it current again by re-capturing — but adopting it here
 * would restore to the wrong page.
 */
internal fun PositionResponse.currentLocatorHint(): PositionHintResponse? =
    hints.firstOrNull { it.kind == HINT_READIUM_LOCATOR && it.current }

/**
 * Maps a server [PositionResponse] — a normal 200 body, or the authoritative state returned
 * in a 409 conflict body — onto a local [BookmarkEntity]. [previous] supplies
 * the Readium locator when the response carries no current one.
 *
 * A previous revision dropped the local locator on an ebook-source response
 * with no locator, to match a server rule that cleared it. Both halves are
 * gone: clearing a position hint that the writing client can't replace leaves
 * the reader with nothing to restore from, which is how a real position got
 * overwritten with chapter 0. A hint's freshness is judged by the anchor it
 * was captured at, not by deleting it.
 *
 * [pairId] comes from the request context: a standalone-scoped response has a
 * null `book_pair_id`, but the local table is keyed by pair.
 */
internal fun PositionResponse.toBookmarkEntity(
    scopeKey: String,
    pairId: Int,
    previous: BookmarkEntity?,
): BookmarkEntity {
    val locator = currentLocatorHint()
    return BookmarkEntity(
        bookPairId = book_pair_id ?: pairId,
        source = source,
        epubChapter = epub_chapter,
        epubSentenceIndex = epub_sentence_index,
        // The server's word on which map its index is expressed in; a later
        // push of this row attests it (issue #116).
        syncMapVersion = sync_map_version,
        audioPositionMs = audio_position_ms,
        epubLocator = locator?.value ?: previous?.epubLocator,
        locatorAudioMs = if (locator != null) locator.audio_position_ms else previous?.locatorAudioMs,
        updatedAt = updated_at,
        capturedAt = captured_at,
        deviceId = device_id,
        deviceName = device_name,
        syncedToServer = true,
        scopeKey = scopeKey,
    )
}

/**
 * Maps a server [PositionResponse] onto a local [UserProgressEntity].
 * [mediaType]/[mediaId] come from the request context since the response alone
 * doesn't always disambiguate which media the progress belongs to (e.g. a
 * standalone audiobook vs. one half of a pair).
 *
 * [previous] supplies `epubCfi`: that is the web reader's hint, which this
 * device can neither produce nor judge, so an Android write must never blank
 * it. It used to arrive as `user_progress.epub_cfi`, a server mirror column
 * that no longer exists (issue #102).
 */
internal fun PositionResponse.toProgressEntity(
    scopeKey: String,
    mediaType: String, mediaId: Int, previous: UserProgressEntity? = null,
) = UserProgressEntity(
    mediaType = mediaType,
    mediaId = mediaId,
    bookPairId = book_pair_id,
    epubCfi = previous?.epubCfi,
    epubChapter = epub_chapter,
    epubProgressPercent = epub_progress_percent,
    audioPositionMs = audio_position_ms,
    isCompleted = is_completed,
    updatedAt = parseSyncTimestamp(preferCapturedAt(captured_at, updated_at)),
    deviceId = device_id,
    deviceName = device_name,
    capturedAt = captured_at,
    syncedToServer = true,
    scopeKey = scopeKey,
)

// ============ BookmarkLog mappers ============

internal fun BookmarkLogResponse.toEntity(scopeKey: String, pairId: Int) = BookmarkLogEntity(
    scopeKey = scopeKey,
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
    deviceId = device_id,
    deviceName = device_name,
)

internal fun BookmarkLogEntity.toResponse() = BookmarkLogResponse(
    id = serverId ?: -localId.toInt(),
    source = source,
    prev_epub_chapter = prevEpubChapter,
    prev_epub_sentence_index = prevEpubSentenceIndex,
    prev_audio_position_ms = prevAudioPositionMs,
    new_epub_chapter = newEpubChapter,
    new_epub_sentence_index = newEpubSentenceIndex,
    new_audio_position_ms = newAudioPositionMs,
    changed_at = changedAt,
    device_id = deviceId,
    device_name = deviceName,
)

/**
 * Timestamp for an optimistic, local-only history entry (see [updateBookmark]),
 * shaped to match what the server actually sends for `changed_at` — 'T'-separated,
 * no trailing zone suffix (contrast `java.time.Instant.toString()`, which always
 * appends "Z" and PlayerScreen.kt's `formatAbsoluteTime` cannot parse).
 */
internal fun localHistoryTimestamp(epochMillis: Long): String =
    java.time.LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(epochMillis), java.time.ZoneOffset.UTC)
        .toString()

/**
 * Result of a canonical-position fetch.
 *
 * [reachable] separates "the server says this book has no position" from "we
 * couldn't ask". The first means starting at the beginning is correct; the
 * second means fall back to the local cache and change nothing.
 */
data class PositionFetch(
    val position: PositionResponse?,
    val reachable: Boolean,
)

/**
 * Everything a reader-position save needs, captured synchronously while the
 * activity, publication and navigator are still alive.
 *
 * [BookSyncRepository.saveReaderPosition] runs on [BookSyncRepository.appScope],
 * a coroutine scope that can outlive the activity, so it must not — and does
 * not need to — touch the Readium `Publication`, navigator, or WebView; this
 * snapshot is everything it needs. Unlike an earlier version of this class,
 * [chapterIndex] here is the COARSE spine chapter, not yet upgraded by a
 * sync-point match — that lookup (`getSyncPointForEpubText`, a Room read) now
 * happens INSIDE `saveReaderPosition` itself (issue #61/#40 fix 2), since it
 * needs only a DB read and belongs on the side that can't be cancelled by
 * activity teardown, same as everything else here.
 */
data class ReaderPositionSnapshot(
    val pairId: Int,
    val chapterIndex: Int,
    val locatorJson: String,
    val textPreview: String,
    val progressPercent: Float?,
    val capturedAtMillis: Long,
    /**
     * Skip the sync-point lookup entirely. Set when a manual audio-sync write
     * (`ReaderActivity.syncSelectedTextToAudio`) is already in flight for this
     * page — resolving a (possibly stale) match against the sync map here too
     * would overwrite that fresher, deliberately-chosen audio position.
     */
    val skipSyncPointLookup: Boolean = false,
)

/**
 * Map a server [PositionResponse] onto the resolver's input type.
 *
 * The resolver is deliberately transport-agnostic (it is shared, fixture-tested
 * logic), so the DTO is converted here rather than the resolver depending on
 * Retrofit types.
 */
internal fun PositionResponse.toStoredPosition() = com.booksync.data.sync.StoredPosition(
    anchorRevision = anchor_revision,
    source = source,
    epubChapter = epub_chapter,
    epubSentenceIndex = epub_sentence_index,
    epubTextPreview = epub_text_preview,
    epubProgressPercent = epub_progress_percent,
    audioPositionMs = audio_position_ms,
    hints = hints.map {
        com.booksync.data.sync.PositionHint(
            kind = it.kind,
            deviceId = it.device_id,
            value = it.value,
            anchorRevision = it.anchor_revision,
            audioPositionMs = it.audio_position_ms,
        )
    },
)

/**
 * Build the resolver's input from the local cache, for use when the server is
 * unreachable.
 *
 * The locally stored locator is offered as a hint at the record's own anchor
 * revision, so it qualifies — this device captured it against this anchor.
 */
internal fun BookmarkEntity.toStoredPosition(deviceId: String) =
    com.booksync.data.sync.StoredPosition(
        anchorRevision = 0L,
        source = source,
        epubChapter = epubChapter,
        epubSentenceIndex = epubSentenceIndex,
        epubTextPreview = null,
        epubProgressPercent = null,
        audioPositionMs = audioPositionMs,
        hints = epubLocator?.let {
            listOf(
                com.booksync.data.sync.PositionHint(
                    kind = com.booksync.data.sync.HINT_READIUM_LOCATOR,
                    deviceId = deviceId,
                    value = it,
                    anchorRevision = 0L,
                    audioPositionMs = locatorAudioMs,
                )
            )
        } ?: emptyList(),
    )
