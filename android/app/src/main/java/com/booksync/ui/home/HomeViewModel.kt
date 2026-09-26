package com.booksync.ui.home

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.ServerVersionGate
import com.booksync.data.remote.VersionBanner
import com.booksync.data.remote.VersionCompat
import com.booksync.data.repository.LibraryLoadState
import com.booksync.data.repository.LibraryLoader
import com.booksync.data.repository.PairOpenTarget
import com.booksync.data.repository.lastPlayedAtMs
import com.booksync.data.repository.ProgressSummary
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * A single card-shaped item on the Home screen. Unifies pairs, standalone audiobooks,
 * and standalone ebooks behind one shape so the "Continue Reading" carousel (and any
 * future mixed-type carousels) can render them uniformly.
 */
data class HomeItem(
    val id: String,                // stable key: "pair_123", "audiobook_42", "ebook_7"
    val title: String,
    val author: String?,
    val mediaType: MediaType,
    val pairId: Int? = null,
    val audiobookId: Int? = null,
    val ebookId: Int? = null,
    val progressPercent: Float = 0f,  // 0..100
    val updatedAtMs: Long = 0L,
    // Audiobook cover path as served by /api/files/covers/{audiobookCoverPath}.
    // Null for ebooks or audiobooks whose cover the server hasn't catalogued yet.
    val audiobookCoverPath: String? = null,
    // A standalone ebook's own cover path. Null for pairs and audiobooks.
    val ebookCoverPath: String? = null,
    val ebookDownloaded: Boolean = false,
    val audiobookDownloaded: Boolean = false,
    val series: String? = null,
    val seriesIndex: Float? = null,
) {
    enum class MediaType { PAIR, EBOOK, AUDIOBOOK }
}

/**
 * Whether the account has any books at all, independent of whether anything has
 * been opened (issue #624). Home's empty state used to say "your library is
 * empty" whenever its own four carousels (Continue Reading / Recently Added /
 * New Pairs / In Queue) had nothing in them — which is also true for an account
 * whose library is full but has not started anything, the exact shape of the
 * demo account a Play reviewer lands on after "Try the demo" (#147).
 *
 * LOADING is not a network state: it means the three source flows below have
 * not all produced a value yet, which is what lets a fresh subscriber tell
 * "nothing has come back from Room yet" apart from "it came back, and there
 * truly are zero books".
 */
enum class HomeLibraryState { LOADING, EMPTY, HAS_BOOKS }

/** One row in the "In Queue" section. */
data class HomeQueueItem(
    val pairId: Int,
    val title: String,
    val status: QueueStatus,
    val percent: Int,
    // From the cached pair (issue #549). The queue endpoint sends only the pair id
    // and title, so all three stay null for a pair Room has not synced yet.
    val audiobookId: Int? = null,
    val audiobookCoverPath: String? = null,
    val author: String? = null,
) {
    enum class QueueStatus { QUEUED, TRANSCRIBING }
}

/**
 * Home feed: 4 parallel carousels backed by the existing repository flows.
 *
 *   - [continueItems]   merged pairs + standalone audiobooks + standalone ebooks (reused
 *                       from what ContinueViewModel surfaced previously)
 *   - [recentlyAdded]   pairs, newest-first, capped at 10
 *   - [newPairs]        unacknowledged pairs flowing from [BookSyncRepository.getNewPairsFlow]
 *   - [queueItems]      transcription queue; stays empty until Phase G wires the repo in
 */
@HiltViewModel
class HomeViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    private val transcriptionRepository: TranscriptionRepository,
    networkMonitor: NetworkMonitor,
    serverUrlManager: ServerUrlManager,
    private val serverVersionGate: ServerVersionGate,
    @ApplicationContext context: Context,
    private val loader: LibraryLoader,
) : ViewModel() {

    private val workManager = WorkManager.getInstance(context)

    /** Mirrors NetworkMonitor so the overflow sheet can disable offline-only actions. */
    val isOnline: StateFlow<Boolean> = networkMonitor.isOnline

    /** Server URL for the "Open web app" empty-state button. */
    val webAppUrl: StateFlow<String> = serverUrlManager.serverUrlFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), serverUrlManager.currentUrl)

    /** Synchronous server URL for building cover image URLs in composables. */
    val serverUrl: String = serverUrlManager.currentUrl

    /**
     * Non-blocking warning when the app and the server are on different API
     * versions, or null when they agree or the server did not say (issue #174).
     *
     * Home is where the check runs because it is the first screen that has both
     * a server URL and a session, and the only one a returning user reaches
     * without passing through login — a check that lived only on the first-run
     * screen would never fire again after the install that configured the server.
     *
     * The decision is here rather than in the composable on purpose: nothing in
     * this repo measures Compose, and a version check that silently stops firing
     * is worse than none, because it reads as "no problem".
     */
    val versionBanner: StateFlow<VersionBanner?> = serverVersionGate.verdict
        .map { VersionCompat.bannerFor(it) }
        .stateIn(
            viewModelScope,
            SharingStarted.Eagerly,
            VersionCompat.bannerFor(serverVersionGate.verdict.value),
        )

    init {
        // Cached process-wide, so this is a no-op once anything has an answer —
        // including the first-run screen's own "Check connection".
        viewModelScope.launch { serverVersionGate.refreshOnce() }
    }

    /** One-shot snackbar messages from transcription actions. */
    private val _transcriptionMessage = MutableStateFlow<String?>(null)
    val transcriptionMessage: StateFlow<String?> = _transcriptionMessage.asStateFlow()
    fun clearTranscriptionMessage() { _transcriptionMessage.value = null }

    /** Pair IDs currently queued/transcribing on the server, used to drive the overflow
     *  sheet's Transcribe-vs-Cancel choice for pairs that aren't in the local DB as synced. */
    val activeTxPairIds: StateFlow<Set<Int>> =
        transcriptionRepository.activeQueueItemsFlow()
            .map { list -> list.map { it.book_pair_id }.toSet() }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptySet())

    // --- Empty-state classification (issue #624, folded into #641) -----------
    /**
     * Exposes [LibraryLoader.state] directly so a caller that needs to know
     * whether a fetch is in flight — as opposed to [libraryState]'s coarser
     * LOADING/EMPTY/HAS_BOOKS — can read the same source Home starts.
     */
    val libraryLoadState: StateFlow<LibraryLoadState> = loader.state

    init {
        // Before issue #641 the only caller of `refreshPairs/refreshEbooks/
        // refreshAudiobooks` was `LibraryViewModel.init`, so a fresh sign-in
        // that landed on Home first — the normal path — saw an empty Room
        // cache with no fetch ever started, and stayed on the EMPTY message
        // until the user happened to open the Library tab. Starting it here
        // too (single-flighted, so opening Library right after does not
        // duplicate the fetch) is the actual fix; `libraryState` below only
        // reads the result.
        //
        // Also retries from `Failed`: a sign-in while offline leaves the
        // loader there (pairs never loaded this sign-in), and nothing else
        // ever moves it off `Failed` on its own — without this, every later
        // Home (switching back to the tab, a relaunch within the same
        // process) would sit on EMPTY forever instead of trying again.
        val state = loader.state.value
        if (state == LibraryLoadState.Idle || state == LibraryLoadState.Failed) loader.refresh()
    }

    /**
     * See [HomeLibraryState]. Built from the same three flows the Library tab's
     * own [com.booksync.ui.library.LibraryViewModel] combines to build its list
     * ([BookSyncRepository.getPairsFlow], [BookSyncRepository.getEbooksFlow],
     * [BookSyncRepository.getAudiobooksFlow]), plus [LibraryLoader.state]
     * (issue #641) — no new query beyond what [LibraryLoader] already runs.
     * `Eagerly` rather than `WhileSubscribed` so the state is correct the
     * instant Home reads it, the same reasoning as [versionBanner] above.
     *
     * Any cached rows win outright as HAS_BOOKS regardless of what the loader
     * is doing — a slow or failed refresh must never hide a library Room
     * already has. With nothing cached, LOADING holds while the loader is
     * still `Idle`/`Loading`, and only resolves to EMPTY once it has settled
     * (`PairsLoaded`/`Loaded`/`Failed`) with nothing to show — otherwise a
     * fresh sign-in would flash EMPTY the instant Room's three (genuinely
     * empty) flows report in, before the fetch this `init` just started has
     * had a chance to answer.
     */
    val libraryState: StateFlow<HomeLibraryState> = combine(
        repository.getPairsFlow(),
        repository.getEbooksFlow(),
        repository.getAudiobooksFlow(),
        libraryLoadState,
    ) { pairs, ebooks, audiobooks, loadState ->
        val hasBooks = pairs.isNotEmpty() || ebooks.isNotEmpty() || audiobooks.isNotEmpty()
        when {
            hasBooks -> HomeLibraryState.HAS_BOOKS
            loadState == LibraryLoadState.Idle || loadState == LibraryLoadState.Loading -> HomeLibraryState.LOADING
            else -> HomeLibraryState.EMPTY
        }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, HomeLibraryState.LOADING)

    // --- Continue Reading ---------------------------------------------------
    private val _continueItems = MutableStateFlow<List<HomeItem>>(emptyList())
    val continueItems: StateFlow<List<HomeItem>> = _continueItems.asStateFlow()

    init {
        viewModelScope.launch {
            combine(
                repository.getRecentlyPlayedPairsFlow(),
                repository.getRecentlyPlayedStandaloneAudiobooksFlow(),
                repository.getRecentlyReadEbooksFlow(),
            ) { pairs, audiobooks, ebooks ->
                val items = mutableListOf<HomeItem>()
                // Issue #618: syncAllBookmarksAndProgress writes a
                // user_progress audiobook row for every pair, so an
                // in-progress paired book otherwise contributes both a
                // pair_N row below and an audiobook_M row from `audiobooks`
                // — despite that flow's name, it is not filtered to books
                // without a pair. Android Auto's Continue Listening merges
                // the same two sources and drops the duplicate the same way.
                val pairedAudiobookIds = pairs.map { it.audiobookId }.toSet()

                for (pair in pairs) {
                    val bookmark = repository.getBookmark(pair.id)
                    val posMs = bookmark?.audioPositionMs ?: 0
                    val totalSec = pair.audiobookDurationSeconds ?: 0
                    val percent = if (totalSec > 0) (posMs / 1000f / totalSec) * 100f else 0f
                    // Issue #476. `BookmarkEntity.updatedAt` holds epoch millis
                    // when saved on this device and ISO-8601 when it came from the
                    // server; `toLongOrNull()` read only the first, so anything
                    // last touched on the web or another device scored 0 and sank
                    // to the bottom of the row meant to surface it. This is the
                    // same read `LibraryRepository.lastOpenedTimesFlow` has always
                    // done — prefer the capture time, then parse either shape —
                    // which is why Library's "Recently opened" was right and this
                    // was not. Since issue #574 it is one shared helper, because
                    // Android Auto's Continue Listening merges the same two
                    // sources and had drifted into its own answer.
                    val updatedAt = bookmark?.lastPlayedAtMs() ?: 0L
                    items += HomeItem(
                        id = "pair_${pair.id}",
                        title = pair.ebookTitle ?: pair.audiobookTitle,
                        author = pair.ebookAuthor ?: pair.audiobookAuthor,
                        mediaType = HomeItem.MediaType.PAIR,
                        pairId = pair.id,
                        audiobookId = pair.audiobookId,
                        ebookId = pair.ebookId,
                        progressPercent = percent,
                        updatedAtMs = updatedAt,
                        audiobookCoverPath = pair.audiobookCoverPath,
                        ebookDownloaded = pair.ebookDownloaded,
                        audiobookDownloaded = pair.audiobookDownloaded,
                        series = pair.ebookSeries,
                        seriesIndex = pair.ebookSeriesIndex,
                    )
                }
                for (ab in audiobooks) {
                    if (ab.id in pairedAudiobookIds) continue
                    val progress = repository.getProgressOnce("audiobook", ab.id)
                    val posMs = progress?.audioPositionMs ?: 0
                    val totalSec = ab.durationSeconds ?: 0
                    val percent = if (totalSec > 0) (posMs / 1000f / totalSec) * 100f else 0f
                    items += HomeItem(
                        id = "audiobook_${ab.id}",
                        title = ab.title,
                        author = ab.author,
                        mediaType = HomeItem.MediaType.AUDIOBOOK,
                        audiobookId = ab.id,
                        progressPercent = percent,
                        updatedAtMs = progress?.lastPlayedAtMs() ?: 0L,
                        audiobookCoverPath = ab.coverFilename,
                        audiobookDownloaded = ab.isDownloaded,
                        series = ab.series,
                        seriesIndex = ab.seriesIndex,
                    )
                }
                for (eb in ebooks) {
                    val progress = repository.getProgressOnce("ebook", eb.id)
                    items += HomeItem(
                        id = "ebook_${eb.id}",
                        title = eb.title,
                        author = eb.author,
                        mediaType = HomeItem.MediaType.EBOOK,
                        ebookId = eb.id,
                        progressPercent = progress?.epubProgressPercent ?: 0f,
                        updatedAtMs = progress?.lastPlayedAtMs() ?: 0L,
                        ebookCoverPath = eb.coverFilename,
                        ebookDownloaded = eb.isDownloaded,
                        series = eb.series,
                        seriesIndex = eb.seriesIndex,
                    )
                }
                items.sortedByDescending { it.updatedAtMs }
            }.collect { _continueItems.value = it }
        }
    }

    // --- Recently Added -----------------------------------------------------
    /**
     * Newest pairs, capped at 10. BookPairEntity has no createdAt, so we sort by id
     * descending — server-side ids are auto-incremented, so this is a faithful proxy.
     */
    val recentlyAdded: StateFlow<List<BookPairEntity>> =
        repository.getPairsFlow()
            .map { pairs -> pairs.sortedByDescending { it.id }.take(10) }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptyList())

    // --- Next up (issue #716) ------------------------------------------------
    /**
     * The next book in each series touched in the last 90 days, finished books
     * included. The rule is [computeNextUp], shared with the web. It reads the
     * whole library rather than the recently-played flows behind Continue
     * Reading, because those drop finished books, the case that matters most.
     */
    val nextUpItems: StateFlow<List<HomeItem>> =
        combine(
            repository.getPairsFlow(),
            repository.getEbooksFlow(),
            repository.getAudiobooksFlow(),
            repository.getAllProgressFlow(),
            repository.getAllBookmarksFlow(),
        ) { pairs, ebooks, audiobooks, progress, bookmarks ->
            val (books, activity) = libraryToNextUpInput(pairs, ebooks, audiobooks, progress, bookmarks)
            computeNextUp(books, activity, System.currentTimeMillis()).map { it.toHomeItem() }
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptyList())

    // --- New Pairs ----------------------------------------------------------
    val newPairs: StateFlow<List<BookPairEntity>> =
        repository.getNewPairsFlow()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptyList())

    /**
     * Dismiss the whole "New Pairs" section (issue #222).
     *
     * Pairs only — the Library's "Acknowledge all" clears ebooks and audiobooks
     * too, and that being the *only* way to clear this section is the bug.
     * Reads the flow rather than [newPairs] so the action works whether or not
     * the carousel currently has a subscriber.
     */
    fun dismissNewPairs() {
        viewModelScope.launch {
            val ids = repository.getNewPairsFlow().first().map { it.id }
            if (ids.isNotEmpty()) repository.acknowledgeItems(ids, "pair")
        }
    }

    // --- In Queue (polls TranscriptionRepository every 10 s) ---------------
    private val _queueItems = MutableStateFlow<List<HomeQueueItem>>(emptyList())
    val queueItems: StateFlow<List<HomeQueueItem>> = _queueItems.asStateFlow()

    init {
        // Map server queue DTOs to HomeQueueItem. The queue endpoint carries no
        // cover or author, so each item borrows them from the cached pair
        // (issue #549) — the same pair "Recently Added" draws its cover from.
        viewModelScope.launch {
            combine(
                transcriptionRepository.activeQueueItemsFlow(),
                repository.getPairsFlow(),
            ) { items, pairs ->
                val pairsById = pairs.associateBy { it.id }
                items.map { item ->
                    val status = TranscriptionStatus.fromQueueItem(item)
                    val pair = pairsById[item.book_pair_id]
                    HomeQueueItem(
                        pairId   = item.book_pair_id,
                        title    = item.book_title ?: "Untitled",
                        status   = when (status) {
                            is TranscriptionStatus.Transcribing -> HomeQueueItem.QueueStatus.TRANSCRIBING
                            else                                -> HomeQueueItem.QueueStatus.QUEUED
                        },
                        percent  = (status as? TranscriptionStatus.Transcribing)?.progressPercent ?: 0,
                        audiobookId        = pair?.audiobookId,
                        audiobookCoverPath = pair?.audiobookCoverPath,
                        author             = pair?.let { it.ebookAuthor ?: it.audiobookAuthor },
                    )
                }
            }.collect { _queueItems.value = it }
        }
    }

    // =======================================================================
    // Overflow-menu actions. Kept intentionally minimal — everything delegates
    // to the same repository / WorkManager / transcription-repo LibraryViewModel
    // uses, so behavior stays consistent across Home and Library.
    // =======================================================================

    /**
     * Resolve where a pair-tap should land based on the user's last-used medium.
     * Falls back to today's preference (ebook → audiobook → details).
     */
    suspend fun resolvePairOpenTarget(pair: BookPairEntity): PairOpenTarget =
        repository.resolvePairOpenTarget(pair, isOnline.value)

    suspend fun resolvePairOpenTarget(pairId: Int): PairOpenTarget =
        repository.resolvePairOpenTarget(pairId, isOnline.value)

    /** See [com.booksync.data.repository.LibraryRepository.progressSummaryForPair] (issue #484). */
    suspend fun progressSummary(pair: BookPairEntity): ProgressSummary =
        repository.progressSummaryForPair(pair)

    /**
     * The Continue row's own items, which may be a pair, a standalone ebook or a
     * standalone audiobook (issue #484).
     */
    suspend fun progressSummary(item: HomeItem): ProgressSummary = when (item.mediaType) {
        HomeItem.MediaType.PAIR ->
            item.pairId?.let { id -> repository.getPairById(id)?.let { repository.progressSummaryForPair(it) } }
        HomeItem.MediaType.EBOOK ->
            item.ebookId?.let { repository.progressSummaryForEbook(it) }
        HomeItem.MediaType.AUDIOBOOK ->
            item.audiobookId?.let { repository.progressSummaryForAudiobook(it) }
    } ?: ProgressSummary(hasProgress = false, isComplete = false)

    fun downloadBoth(pair: BookPairEntity)         = enqueue(pair.id, "ALL", "download_pair_${pair.id}")
    fun downloadBothById(pairId: Int)              = enqueue(pairId,  "ALL", "download_pair_$pairId")
    fun downloadEbook(pair: BookPairEntity)     = enqueue(pair.id, "EBOOK",     "download_ebook_${pair.id}")
    fun downloadAudiobook(pair: BookPairEntity) = enqueue(pair.id, "AUDIOBOOK", "download_audio_${pair.id}")
    // SYNC_MAP_EXPLICIT, not SYNC_MAP (issue #655 follow-up): an explicit tap
    // must bypass the "Only download sync maps over Wi-Fi" setting.
    fun refreshSyncData(pair: BookPairEntity)   = enqueue(pair.id, "SYNC_MAP_EXPLICIT",  "download_sync_${pair.id}")

    fun downloadStandaloneEbook(ebook: EBookEntity) =
        enqueue(ebook.id, "STANDALONE_EBOOK", "download_standalone_ebook_${ebook.id}")

    fun downloadStandaloneAudiobook(audio: AudioBookEntity) =
        enqueue(audio.id, "STANDALONE_AUDIOBOOK", "download_standalone_audio_${audio.id}")

    private fun enqueue(id: Int, type: String, uniqueName: String) {
        val request = DownloadWorker.request(id, type)
        workManager.enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
    }

    fun deleteEbookOf(pair: BookPairEntity)               = runSafely { repository.deleteEbook(pair) }
    fun deleteAudiobookOf(pair: BookPairEntity)           = runSafely { repository.deleteAudiobook(pair) }
    fun deleteStandaloneEbook(ebook: EBookEntity)         = runSafely { repository.deleteStandaloneEbook(ebook) }
    fun deleteStandaloneAudiobook(audio: AudioBookEntity) = runSafely { repository.deleteStandaloneAudiobook(audio) }
    fun unlinkPair(pair: BookPairEntity)                  = runSafely { repository.deletePair(pair.id) }

    fun markComplete(pair: BookPairEntity) = runSafely {
        repository.markPairComplete(pair.id, pair.ebookId, pair.audiobookId)
    }
    fun markCompleteEbook(id: Int)     = runSafely { repository.markComplete("ebook", id) }
    fun markCompleteAudiobook(id: Int) = runSafely { repository.markComplete("audiobook", id) }

    // Pair-level DELETE removes the canonical bookmark + hints + user_progress
    // server-side and clears the matching local Room caches. The old per-leg
    // zero-write left the bookmark in place, which re-seeded progress right
    // back (issue: reset buttons not actually resetting).
    fun resetProgress(pair: BookPairEntity) = runSafely {
        repository.resetPairProgress(pair.id)
    }
    fun resetProgressEbook(id: Int)     = runSafely { repository.resetStandaloneProgress("ebook", id) }
    fun resetProgressAudiobook(id: Int) = runSafely { repository.resetStandaloneProgress("audiobook", id) }

    fun addToTranscriptionQueue(pair: BookPairEntity) {
        viewModelScope.launch {
            transcriptionRepository.addToQueue(pair.id)
                .onSuccess { _transcriptionMessage.value = "Added to transcription queue" }
                .onFailure { e ->
                    _transcriptionMessage.value = when (e) {
                        is TranscriptionRepository.OfflineException -> "Connect to server to transcribe"
                        else -> "Failed to add to queue: ${e.message ?: "unknown error"}"
                    }
                }
        }
    }

    fun cancelTranscription(pair: BookPairEntity) {
        viewModelScope.launch {
            transcriptionRepository.cancel(pair.id)
                .onSuccess { _transcriptionMessage.value = "Transcription cancelled" }
                .onFailure { e ->
                    _transcriptionMessage.value = when (e) {
                        is TranscriptionRepository.OfflineException -> "Connect to server to cancel"
                        else -> "Failed to cancel: ${e.message ?: "unknown error"}"
                    }
                }
        }
    }

    private inline fun runSafely(crossinline block: suspend () -> Unit) {
        viewModelScope.launch {
            try { block() } catch (_: Exception) { /* swallowed — UI surfaces via other flows */ }
        }
    }
}
