package com.booksync.ui.library

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.PairOpenTarget
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.inject.Inject
import kotlinx.coroutines.flow.map

// ============================================================================
// UI state
// ============================================================================

/** Top-level media filter chosen from the pill row. */
enum class LibraryFilter { ALL, PAIRS, EBOOKS, AUDIOBOOKS, NEW }

/** Sort order. Series-only values (SeriesCount) are valid only in series mode. */
enum class LibrarySort(val label: String) {
    RecentlyAdded("Recently added"),
    RecentlyOpened("Recently opened"),
    TitleAsc("Title A–Z"),
    AuthorAsc("Author A–Z"),
    SeriesOrder("Series order"),
    SeriesCount("Most books"),
}

/** Immutable snapshot of the library filter / sort / series state. */
data class LibraryUiState(
    val filter: LibraryFilter = LibraryFilter.ALL,
    val groupBySeries: Boolean = false,
    val seriesFilter: String? = null,
    val transcribedOnly: Boolean = false,
    val sort: LibrarySort = LibrarySort.RecentlyAdded,
    val searchQuery: String = "",
)

/**
 * Unified item the grid renders. Exactly one of (pair, ebook, audiobook) is non-null.
 * Series stacks are derived downstream and carry no entity.
 */
data class LibraryItem(
    val key: String,
    val pair: BookPairEntity? = null,
    val ebook: EBookEntity? = null,
    val audiobook: AudioBookEntity? = null,
) {
    val title: String
        get() = pair?.ebookTitle ?: ebook?.title ?: audiobook?.title ?: "Untitled"
    val author: String?
        get() = pair?.ebookAuthor ?: pair?.audiobookAuthor ?: ebook?.author ?: audiobook?.author
    val series: String?
        get() = pair?.ebookSeries ?: ebook?.series ?: audiobook?.series
    val seriesIndex: Float?
        get() = pair?.ebookSeriesIndex ?: ebook?.seriesIndex ?: audiobook?.seriesIndex
}

/** One stack in series-grouped mode. */
data class SeriesStack(
    val seriesName: String,
    val items: List<LibraryItem>,
) {
    val pairCount: Int      get() = items.count { it.pair != null }
    val ebookCount: Int     get() = items.count { it.ebook != null && it.pair == null }
    val audiobookCount: Int get() = items.count { it.audiobook != null && it.pair == null }
    val totalCount: Int     get() = items.size
    val author: String?     get() {
        val unique = items.mapNotNull { it.author }.distinct()
        return when {
            unique.isEmpty() -> null
            unique.size == 1 -> unique.first()
            else -> "Various Authors"
        }
    }
}

// ============================================================================
// ViewModel
// ============================================================================

@HiltViewModel
class LibraryViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    private val transcriptionRepository: TranscriptionRepository,
    private val networkMonitor: NetworkMonitor,
    serverUrlManager: com.booksync.data.remote.ServerUrlManager,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {

    private val workManager = WorkManager.getInstance(context)

    val serverUrl: String = serverUrlManager.currentUrl

    /** Exposes live network status so [LibraryScreen] can gate the Transcribe action. */
    val isOnline: StateFlow<Boolean> = networkMonitor.isOnline
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), networkMonitor.isOnline.value)

    /**
     * Set of pair IDs that are currently queued or being transcribed.
     * Polled every 10 s by [TranscriptionRepository.activeQueueItemsFlow].
     * Used by [LibraryScreen] to set [OverflowTarget.Pair.isQueuedOrTranscribing].
     */
    val activeTranscribingPairIds: StateFlow<Set<Int>> =
        transcriptionRepository.activeQueueItemsFlow()
            .map { items -> items.map { it.book_pair_id }.toSet() }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptySet())

    // --- Filter state ------------------------------------------------------

    private val _uiState = MutableStateFlow(LibraryUiState())
    val uiState: StateFlow<LibraryUiState> = _uiState.asStateFlow()

    fun setFilter(filter: LibraryFilter)     { _uiState.value = _uiState.value.copy(filter = filter) }
    fun setSort(sort: LibrarySort)            { _uiState.value = _uiState.value.copy(sort = sort) }
    fun setTranscribedOnly(v: Boolean)        { _uiState.value = _uiState.value.copy(transcribedOnly = v) }
    fun setGroupBySeries(v: Boolean)          { _uiState.value = _uiState.value.copy(groupBySeries = v, seriesFilter = null) }
    /**
     * Drill from the grouped series grid into a flat list scoped to one series.
     * We also flip `groupBySeries` off — otherwise the drilled view tries to
     * render as a stack of one, which is what the user saw as "nothing happens."
     * `clearSeriesFilter()` leaves grouping off; the user can re-enable from the
     * toggle.
     */
    fun drillIntoSeries(name: String)         { _uiState.value = _uiState.value.copy(seriesFilter = name, groupBySeries = false, sort = LibrarySort.SeriesOrder) }
    fun clearSeriesFilter()                   { _uiState.value = _uiState.value.copy(seriesFilter = null) }
    fun setSearchQuery(q: String)             { _uiState.value = _uiState.value.copy(searchQuery = q) }

    /**
     * Reset every narrowing filter back to defaults — called from the
     * "Clear" action on the filtered-count bar. Keeps sort + groupBySeries
     * alone: those are user preferences, not filters.
     */
    fun clearAllFilters() {
        _uiState.value = _uiState.value.copy(
            filter = LibraryFilter.ALL,
            transcribedOnly = false,
            seriesFilter = null,
            searchQuery = "",
        )
    }

    /**
     * One-shot initializer for deep-links: e.g. Home → "library?filter=NEW&sort=RecentlyAdded".
     * Idempotent — only the provided fields are updated.
     */
    fun applyDeepLink(
        filter: LibraryFilter? = null,
        series: String? = null,
        sort: LibrarySort? = null,
        groupBySeries: Boolean? = null,
    ) {
        _uiState.value = _uiState.value.copy(
            filter        = filter ?: _uiState.value.filter,
            seriesFilter  = series,
            sort          = sort ?: _uiState.value.sort,
            groupBySeries = groupBySeries ?: _uiState.value.groupBySeries,
        )
    }

    // --- Data sources ------------------------------------------------------

    private val pairsFlow      = repository.getPairsFlow()
    private val ebooksFlow     = repository.getEbooksFlow()
    private val audiobooksFlow = repository.getAudiobooksFlow()
    private val newPairsFlow   = repository.getNewPairsFlow()
    private val newEbooksFlow  = repository.getNewEbooksFlow()
    private val newAudiobooksFlow = repository.getNewAudiobooksFlow()

    /** Bundled server library snapshot (flow `combine` maxes at 5 heterogeneous sources). */
    private data class AllSources(
        val pairs: List<BookPairEntity>,
        val ebooks: List<EBookEntity>,
        val audios: List<AudioBookEntity>,
    )
    private data class NewSources(
        val pairs: List<BookPairEntity>,
        val ebooks: List<EBookEntity>,
        val audios: List<AudioBookEntity>,
    )

    private val allSources = combine(pairsFlow, ebooksFlow, audiobooksFlow) { p, e, a -> AllSources(p, e, a) }
    private val newSources = combine(newPairsFlow, newEbooksFlow, newAudiobooksFlow) { p, e, a -> NewSources(p, e, a) }

    /** The filtered + sorted list of items (flat grid mode). */
    val items: StateFlow<List<LibraryItem>> = combine(_uiState, allSources, newSources) { ui, all, new ->
        val pairs      = all.pairs
        val ebooks     = all.ebooks
        val audiobooks = all.audios
        val newPairs   = new.pairs
        val newEbooks  = new.ebooks
        val newAudios  = new.audios

        val pairedEbookIds = pairs.map { it.ebookId }.toSet()
        val pairedAudioIds = pairs.map { it.audiobookId }.toSet()
        val standaloneEbooks = ebooks.filter { it.id !in pairedEbookIds }
        val standaloneAudios = audiobooks.filter { it.id !in pairedAudioIds }

        // Step 1 — materialize a master list per filter
        val filtered: List<LibraryItem> = when (ui.filter) {
            LibraryFilter.ALL -> buildList {
                pairs.forEach { add(LibraryItem("pair_${it.id}", pair = it)) }
                standaloneEbooks.forEach { add(LibraryItem("ebook_${it.id}", ebook = it)) }
                standaloneAudios.forEach { add(LibraryItem("audiobook_${it.id}", audiobook = it)) }
            }
            LibraryFilter.PAIRS       -> pairs.map { LibraryItem("pair_${it.id}", pair = it) }
            LibraryFilter.EBOOKS      -> ebooks.map { LibraryItem("ebook_${it.id}", ebook = it) }
            LibraryFilter.AUDIOBOOKS  -> audiobooks.map { LibraryItem("audiobook_${it.id}", audiobook = it) }
            LibraryFilter.NEW         -> buildList {
                newPairs.forEach { add(LibraryItem("pair_${it.id}", pair = it)) }
                newEbooks.forEach { add(LibraryItem("ebook_${it.id}", ebook = it)) }
                newAudios.forEach { add(LibraryItem("audiobook_${it.id}", audiobook = it)) }
            }
        }

        // Step 2 — transcribed-only filter.
        // Only pairs have a transcription — so when this toggle is on we drop
        // standalone ebooks/audiobooks too. Uses server-side status rather
        // than local cache so the filter is useful before the user has
        // downloaded anything.
        val transcribedFiltered = if (ui.transcribedOnly) {
            filtered.filter { it.pair?.status == "synced" }
        } else filtered

        // Step 3 — series drill-in
        val seriesFiltered = ui.seriesFilter?.let { name ->
            transcribedFiltered.filter { it.series.equals(name, ignoreCase = true) }
        } ?: transcribedFiltered

        // Step 4 — sort
        val sorted = seriesFiltered.sortedWith(comparatorFor(ui.sort))

        // Step 5 — text search filter (title / author / series)
        if (ui.searchQuery.isBlank()) sorted
        else {
            val q = ui.searchQuery.trim().lowercase()
            sorted.filter { item ->
                item.title.lowercase().contains(q) ||
                item.author?.lowercase()?.contains(q) == true ||
                item.series?.lowercase()?.contains(q) == true
            }
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptyList())

    /** Series-mode view: groups items by series and returns one [SeriesStack] per series. */
    val seriesStacks: StateFlow<List<SeriesStack>> =
        combine(items, _uiState) { list, ui ->
            if (!ui.groupBySeries) return@combine emptyList()
            val groups = list.groupBy { it.series?.trim().takeIf { s -> !s.isNullOrEmpty() } ?: "No Series" }
            val sorted = when (ui.sort) {
                LibrarySort.TitleAsc    -> groups.entries.sortedBy { it.key.lowercase() }
                LibrarySort.SeriesCount -> groups.entries.sortedByDescending { it.value.size }
                else                    -> groups.entries.sortedBy { it.key.lowercase() }
            }
            sorted.map { (name, items) -> SeriesStack(name, items) }
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptyList())

    // --- Derived counts ----------------------------------------------------

    val newItemsCount: StateFlow<Int> = combine(
        repository.getNewEbookCountFlow(),
        repository.getNewAudiobookCountFlow(),
        repository.getNewPairCountFlow(),
    ) { e, a, p -> e + a + p }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), 0)

    // --- Refresh / messages ------------------------------------------------

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _refreshMessage = MutableStateFlow<String?>(null)
    val refreshMessage = _refreshMessage.asStateFlow()

    private val _downloadError = MutableStateFlow<String?>(null)
    val downloadError = _downloadError.asStateFlow()

    fun clearRefreshMessage() { _refreshMessage.value = null }
    fun clearDownloadError()  { _downloadError.value = null }

    fun refresh(silent: Boolean = false) {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                repository.refreshPairs()
                repository.refreshEbooks()
                repository.refreshAudiobooks()
                val pairs = repository.getPairsFlow().first()
                viewModelScope.launch(Dispatchers.IO) {
                    repository.processPendingSync()
                    repository.syncAllBookmarksAndProgress(pairs)
                }
                if (!silent) _refreshMessage.value = "Library refreshed"
            } catch (e: UnknownHostException) {
                _refreshMessage.value = "Offline — showing cached library"
            } catch (e: ConnectException) {
                _refreshMessage.value = "Server unreachable"
            } catch (e: SocketTimeoutException) {
                _refreshMessage.value = "Server unreachable (timeout)"
            } catch (e: HttpException) {
                _refreshMessage.value = "Server error: HTTP ${e.code()}"
            } catch (e: Exception) {
                _refreshMessage.value = "Refresh failed: ${e.message ?: "unknown error"}"
            }
            _refreshing.value = false
        }
    }

    // --- Download progress (observed from WorkManager) ---------------------

    private val _downloadingProgress = MutableStateFlow<Map<Int, Int>>(emptyMap())
    /** Pair ID → percent (0..100). Key absent when no download is active for that pair. */
    val downloadingProgress: StateFlow<Map<Int, Int>> = _downloadingProgress.asStateFlow()

    init {
        refresh(silent = true)
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                val progress = mutableMapOf<Int, Int>()
                var errorMsg: String? = null
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val pairId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        val pct = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                        if (pairId != -1) progress[pairId] = pct.coerceIn(0, 100)
                    } else if (info.state == WorkInfo.State.FAILED) {
                        info.outputData.getString(DownloadWorker.ERROR_KEY)?.let { errorMsg = it }
                    }
                }
                _downloadingProgress.value = progress
                errorMsg?.let { _downloadError.value = it }
            }
        }
    }

    // --- Actions (pair) ----------------------------------------------------

    /**
     * Resolve where a pair-tap should land based on the user's last-used medium
     * (bookmark.source). Falls back to today's preference (ebook → audiobook → details).
     */
    suspend fun resolvePairOpenTarget(pair: BookPairEntity): PairOpenTarget =
        repository.resolvePairOpenTarget(pair)

    fun downloadAll(pair: BookPairEntity)         = enqueue(pair.id, "ALL",       "download_pair_${pair.id}")
    fun downloadEbook(pair: BookPairEntity)       = enqueue(pair.id, "EBOOK",     "download_ebook_${pair.id}")
    fun downloadAudiobook(pair: BookPairEntity)   = enqueue(pair.id, "AUDIOBOOK", "download_audio_${pair.id}")
    fun refreshSyncData(pair: BookPairEntity)     = enqueue(pair.id, "SYNC_MAP",  "download_sync_${pair.id}")

    fun downloadStandaloneEbook(ebook: EBookEntity) =
        enqueue(ebook.id, "STANDALONE_EBOOK", "download_standalone_ebook_${ebook.id}")

    fun downloadStandaloneAudiobook(audio: AudioBookEntity) =
        enqueue(audio.id, "STANDALONE_AUDIOBOOK", "download_standalone_audio_${audio.id}")

    private fun enqueue(pairId: Int, type: String, uniqueName: String) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pairId,
                DownloadWorker.KEY_TYPE    to type,
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
        _downloadingProgress.value = _downloadingProgress.value + (pairId to 0)
    }

    fun deleteEbookOf(pair: BookPairEntity)           = runSafely { repository.deleteEbook(pair) }
    fun deleteAudiobookOf(pair: BookPairEntity)       = runSafely { repository.deleteAudiobook(pair) }
    fun deleteStandaloneEbook(ebook: EBookEntity)     = runSafely { repository.deleteStandaloneEbook(ebook) }
    fun deleteStandaloneAudiobook(audio: AudioBookEntity) = runSafely { repository.deleteStandaloneAudiobook(audio) }
    fun unlinkPair(pair: BookPairEntity)              = runSafely { repository.deletePair(pair.id); refresh() }
    fun markComplete(pair: BookPairEntity)        = runSafely {
        repository.markComplete("audiobook", pair.audiobookId)
        repository.markComplete("ebook", pair.ebookId)
    }
    fun markCompleteEbook(id: Int)                = runSafely { repository.markComplete("ebook", id) }
    fun markCompleteAudiobook(id: Int)            = runSafely { repository.markComplete("audiobook", id) }
    // Pair-level DELETE removes the canonical bookmark + hints + user_progress
    // server-side and clears the matching local Room caches. The old per-leg
    // zero-write left the bookmark in place, which re-seeded progress right
    // back (issue: reset buttons not actually resetting).
    fun resetProgress(pair: BookPairEntity)       = runSafely {
        repository.resetPairProgress(pair.id)
    }
    fun resetProgressEbook(id: Int)               = runSafely { repository.resetMediaProgress("ebook", id) }
    fun resetProgressAudiobook(id: Int)           = runSafely { repository.resetMediaProgress("audiobook", id) }

    // --- Actions (series batch) --------------------------------------------
    //
    // A series can mix pairs, standalone ebooks, and standalone audiobooks.
    // Each call iterates and dispatches the appropriate per-item op — the
    // worker / repository handle already-downloaded / already-complete
    // idempotently, so no skip logic needed here.

    fun downloadSeries(items: List<LibraryItem>) {
        items.forEach { item ->
            when {
                item.pair != null      -> downloadAll(item.pair)
                item.ebook != null     -> downloadStandaloneEbook(item.ebook)
                item.audiobook != null -> downloadStandaloneAudiobook(item.audiobook)
            }
        }
    }

    fun markSeriesComplete(items: List<LibraryItem>) {
        items.forEach { item ->
            when {
                item.pair != null      -> markComplete(item.pair)
                item.ebook != null     -> markCompleteEbook(item.ebook.id)
                item.audiobook != null -> markCompleteAudiobook(item.audiobook.id)
            }
        }
    }

    fun resetSeriesProgress(items: List<LibraryItem>) {
        items.forEach { item ->
            when {
                item.pair != null      -> resetProgress(item.pair)
                item.ebook != null     -> resetProgressEbook(item.ebook.id)
                item.audiobook != null -> resetProgressAudiobook(item.audiobook.id)
            }
        }
    }

    // --- Transcription actions ---------------------------------------------

    /** Snackbar message for transcription results (success or error). */
    private val _transcriptionMessage = MutableStateFlow<String?>(null)
    val transcriptionMessage: StateFlow<String?> = _transcriptionMessage.asStateFlow()

    fun clearTranscriptionMessage() { _transcriptionMessage.value = null }

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

    // --- Actions (acknowledge NEW items) -----------------------------------

    fun acknowledgeAllNew() {
        viewModelScope.launch {
            val pairs = newPairsFlow.first()
            val ebooks = newEbooksFlow.first()
            val audios = newAudiobooksFlow.first()
            if (pairs.isNotEmpty())  repository.acknowledgeItems(pairs.map { it.id }, "pair")
            if (ebooks.isNotEmpty()) repository.acknowledgeItems(ebooks.map { it.id }, "ebook")
            if (audios.isNotEmpty()) repository.acknowledgeItems(audios.map { it.id }, "audiobook")
        }
    }

    // --- Helpers -----------------------------------------------------------

    private inline fun runSafely(crossinline block: suspend () -> Unit) {
        viewModelScope.launch {
            try { block() } catch (_: Exception) { /* swallowed — UI shows snackbar via flows */ }
        }
    }

    private fun comparatorFor(sort: LibrarySort): Comparator<LibraryItem> = when (sort) {
        LibrarySort.RecentlyAdded  -> compareByDescending { it.pair?.id ?: it.ebook?.id ?: it.audiobook?.id ?: 0 }
        LibrarySort.RecentlyOpened -> compareByDescending { it.pair?.id ?: it.ebook?.id ?: it.audiobook?.id ?: 0 } // proxy — UI reads progress flows for true ordering
        LibrarySort.TitleAsc       -> compareBy { it.title.lowercase() }
        LibrarySort.AuthorAsc      -> compareBy(nullsLast()) { it.author?.lowercase() }
        LibrarySort.SeriesOrder    -> compareBy(nullsLast()) { it.seriesIndex }
        LibrarySort.SeriesCount    -> compareBy { it.title.lowercase() } // only meaningful in series mode
    }
}
