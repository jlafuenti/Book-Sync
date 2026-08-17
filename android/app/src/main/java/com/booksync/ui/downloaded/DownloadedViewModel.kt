package com.booksync.ui.downloaded

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
import com.booksync.ui.library.LibrarySort
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class DownloadedFilter { ALL, PAIRS, EBOOKS, AUDIOBOOKS }

data class DownloadedUiState(
    val filter: DownloadedFilter = DownloadedFilter.ALL,
    val sort: LibrarySort = LibrarySort.RecentlyAdded,
    val searchQuery: String = "",
)

data class DownloadedItem(
    val pair: BookPairEntity? = null,
    val ebook: EBookEntity? = null,
    val audiobook: AudioBookEntity? = null,
) {
    val key: String get() = pair?.let { "pair_${it.id}" } ?: ebook?.let { "ebook_${it.id}" } ?: audiobook?.let { "audio_${it.id}" } ?: ""
    val title: String get() = pair?.ebookTitle ?: ebook?.title ?: audiobook?.title ?: ""
    val author: String? get() = pair?.ebookAuthor ?: pair?.audiobookAuthor ?: ebook?.author ?: audiobook?.author
    val series: String? get() = pair?.ebookSeries ?: ebook?.series ?: audiobook?.series
    val seriesIndex: Float? get() = pair?.ebookSeriesIndex ?: ebook?.seriesIndex ?: audiobook?.seriesIndex
    val sortId: Int get() = pair?.id ?: ebook?.id ?: audiobook?.id ?: 0
}

@HiltViewModel
class DownloadedViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    serverUrlManager: com.booksync.data.remote.ServerUrlManager,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {

    private val workManager = WorkManager.getInstance(context)

    val serverUrl: String = serverUrlManager.currentUrl

    // --- Filter / sort state -------------------------------------------------

    private val _uiState = MutableStateFlow(DownloadedUiState())
    val uiState = _uiState.asStateFlow()

    fun setFilter(f: DownloadedFilter) { _uiState.value = _uiState.value.copy(filter = f) }
    fun setSort(s: LibrarySort)        { _uiState.value = _uiState.value.copy(sort = s) }
    fun setSearch(q: String)           { _uiState.value = _uiState.value.copy(searchQuery = q) }

    // --- Raw flows (kept for overflow menu entity look-ups) ------------------

    val downloadedPairs      = repository.getDownloadedPairsFlow()
    val downloadedEbooks     = repository.getDownloadedEbooksFlow()
    val downloadedAudiobooks = repository.getDownloadedAudiobooksFlow()

    // --- Unified filtered + sorted item list ---------------------------------

    val items = combine(downloadedPairs, downloadedEbooks, downloadedAudiobooks, _uiState) { pairs, ebooks, audiobooks, ui ->
        val articleRegex = "^(the|a|an)\\s+".toRegex(RegexOption.IGNORE_CASE)

        val allItems = buildList {
            if (ui.filter == DownloadedFilter.ALL || ui.filter == DownloadedFilter.PAIRS)
                pairs.forEach { add(DownloadedItem(pair = it)) }
            if (ui.filter == DownloadedFilter.ALL || ui.filter == DownloadedFilter.EBOOKS)
                ebooks.forEach { add(DownloadedItem(ebook = it)) }
            if (ui.filter == DownloadedFilter.ALL || ui.filter == DownloadedFilter.AUDIOBOOKS)
                audiobooks.forEach { add(DownloadedItem(audiobook = it)) }
        }

        val searched = if (ui.searchQuery.isBlank()) allItems else {
            val q = ui.searchQuery.lowercase()
            allItems.filter { item ->
                item.title.lowercase().contains(q) ||
                    item.author?.lowercase()?.contains(q) == true ||
                    item.series?.lowercase()?.contains(q) == true
            }
        }

        searched.sortedWith(comparatorFor(ui.sort, articleRegex))
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), emptyList())

    private fun comparatorFor(sort: LibrarySort, articleRegex: Regex): Comparator<DownloadedItem> = when (sort) {
        LibrarySort.RecentlyAdded  -> compareByDescending { it.sortId }
        LibrarySort.RecentlyOpened -> compareByDescending { it.sortId }
        LibrarySort.TitleAsc       -> compareBy { it.title.replace(articleRegex, "").lowercase() }
        LibrarySort.AuthorAsc      -> compareBy(nullsLast()) { it.author?.lowercase() }
        LibrarySort.SeriesOrder    -> compareBy(nullsLast()) { it.seriesIndex }
        LibrarySort.SeriesCount    -> compareBy { it.title.replace(articleRegex, "").lowercase() }
    }

    // --- Download progress (pair id → percent 0..100) -----------------------

    private val _downloadingProgress = MutableStateFlow<Map<Int, Int>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    init {
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                val progress = mutableMapOf<Int, Int>()
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val pairId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        val pct = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                        if (pairId != -1) progress[pairId] = pct.coerceIn(0, 100)
                    }
                }
                _downloadingProgress.value = progress
            }
        }
    }

    // --- Actions -------------------------------------------------------------

    fun downloadAll(pair: BookPairEntity)           = enqueue(pair.id, "ALL",       "download_pair_${pair.id}")
    fun downloadEbookOnly(pair: BookPairEntity)     = enqueue(pair.id, "EBOOK",     "download_ebook_${pair.id}")
    fun downloadAudiobookOnly(pair: BookPairEntity) = enqueue(pair.id, "AUDIOBOOK", "download_audio_${pair.id}")

    fun refreshSyncData(pair: BookPairEntity) {
        viewModelScope.launch { repository.resetSyncMapDownloaded(pair.id) }
        enqueue(pair.id, "SYNC_MAP", "sync_map_${pair.id}")
    }

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

    fun deleteEbook(pair: BookPairEntity)                    = runSafely { repository.deleteEbook(pair) }
    fun deleteAudiobook(pair: BookPairEntity)                = runSafely { repository.deleteAudiobook(pair) }
    fun deleteStandaloneEbook(ebook: EBookEntity)            = runSafely { repository.deleteStandaloneEbook(ebook) }
    fun deleteStandaloneAudiobook(audio: AudioBookEntity)    = runSafely { repository.deleteStandaloneAudiobook(audio) }

    fun markComplete(pair: BookPairEntity) = runSafely {
        repository.markPairComplete(pair.id, pair.ebookId, pair.audiobookId)
    }

    // Pair-level DELETE removes the canonical bookmark + hints + user_progress
    // server-side and clears the matching local Room caches. The old per-leg
    // zero-write left the bookmark in place, which re-seeded progress right
    // back (issue: reset buttons not actually resetting).
    fun resetProgress(pair: BookPairEntity) = runSafely {
        repository.resetPairProgress(pair.id)
    }

    fun unlinkPair(pair: BookPairEntity) = runSafely { repository.deletePair(pair.id) }

    /**
     * Wipe every local file. Used by Account → "Clear all downloads".
     */
    fun clearAllDownloads() {
        viewModelScope.launch {
            runCatching {
                downloadedPairs.first().forEach {
                    repository.deleteEbook(it)
                    repository.deleteAudiobook(it)
                }
                downloadedEbooks.first().forEach { repository.deleteStandaloneEbook(it) }
                downloadedAudiobooks.first().forEach { repository.deleteStandaloneAudiobook(it) }
            }
        }
    }

    private inline fun runSafely(crossinline block: suspend () -> Unit) {
        viewModelScope.launch {
            runCatching { block() }
        }
    }
}
