package com.booksync.ui.details

import android.content.Context
import androidx.lifecycle.SavedStateHandle
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
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * What kind of entity this details screen is showing.
 * Decoded from whichever SavedStateHandle arg is present:
 *   - `pairId`      → [Pair]
 *   - `ebookId`     → [Ebook] (standalone)
 *   - `audiobookId` → [Audiobook] (standalone)
 */
sealed class DetailsTarget {
    data class Pair(val pairId: Int) : DetailsTarget()
    data class Ebook(val ebookId: Int) : DetailsTarget()
    data class Audiobook(val audiobookId: Int) : DetailsTarget()
}

/**
 * Extended metadata fetched over the network for the Book Details screen.
 * These fields aren't persisted in the local Room cache today, so we pull
 * them fresh each time details opens. Null on network error.
 */
data class BookExtendedMeta(
    val description: String? = null,
    val series: String? = null,
    val seriesIndex: Float? = null,
    val publisher: String? = null,
    val publishYear: Int? = null,
    val language: String? = null,
    val narrators: String? = null,
)

/**
 * Immutable snapshot for the Book Details screen.
 * Exactly one of `pair` / `ebook` / `audiobook` is non-null.
 */
data class BookDetailsUi(
    val loading: Boolean = true,
    val pair: BookPairEntity? = null,
    val ebook: EBookEntity? = null,
    val audiobook: AudioBookEntity? = null,
    val downloadPercent: Int? = null,           // 0..100 when actively downloading
    val message: String? = null,                // transient snackbar text
    val extendedMeta: BookExtendedMeta? = null, // description, series, etc. (network-fetched)
) {
    val title: String
        get() = pair?.ebookTitle ?: ebook?.title ?: audiobook?.title ?: ""
    val author: String?
        get() = pair?.ebookAuthor ?: pair?.audiobookAuthor ?: ebook?.author ?: audiobook?.author
    /** Pretty-printed series label, e.g. "Discworld #39". */
    val seriesLabel: String?
        get() {
            val name = extendedMeta?.series ?: ebook?.series ?: audiobook?.series
            if (name.isNullOrBlank()) return null
            val idx = extendedMeta?.seriesIndex ?: ebook?.seriesIndex ?: audiobook?.seriesIndex
            val suffix = idx?.let { v ->
                // Drop trailing .0 for integer indices
                if (v % 1f == 0f) " #${v.toInt()}" else " #$v"
            } ?: ""
            return "$name$suffix"
        }
    val description: String?
        get() = extendedMeta?.description?.takeIf { it.isNotBlank() }
    val audiobookIdForCover: Int?
        get() = pair?.audiobookId ?: audiobook?.id
    val audiobookCoverPath: String?
        get() = pair?.audiobookCoverPath ?: audiobook?.coverFilename
}

@HiltViewModel
class BookDetailsViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    private val api: BookSyncApi,
    serverUrlManager: com.booksync.data.remote.ServerUrlManager,
    @param:ApplicationContext private val context: Context,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val workManager = WorkManager.getInstance(context)

    val serverUrl: String = serverUrlManager.currentUrl

    // ------------------------------------------------------------------
    // Resolve the target from SavedStateHandle. Exactly one of the three
    // arg keys is set based on which nav route was used.
    // ------------------------------------------------------------------
    val target: DetailsTarget = run {
        val pairId      = savedStateHandle.get<Int>("pairId")
        val ebookId     = savedStateHandle.get<Int>("ebookId")
        val audiobookId = savedStateHandle.get<Int>("audiobookId")
        when {
            pairId != null      -> DetailsTarget.Pair(pairId)
            ebookId != null     -> DetailsTarget.Ebook(ebookId)
            audiobookId != null -> DetailsTarget.Audiobook(audiobookId)
            else -> error("BookDetailsViewModel requires one of pairId / ebookId / audiobookId")
        }
    }

    // ------------------------------------------------------------------
    // Reactive entity flows — exactly one is populated based on target.
    // ------------------------------------------------------------------
    private val pairFlow = when (val t = target) {
        is DetailsTarget.Pair -> repository.getPairByIdFlow(t.pairId)
        else -> flowOf<BookPairEntity?>(null)
    }
    private val ebookFlow = when (val t = target) {
        is DetailsTarget.Ebook -> repository.getEbookByIdFlow(t.ebookId)
        else -> flowOf<EBookEntity?>(null)
    }
    private val audioFlow = when (val t = target) {
        is DetailsTarget.Audiobook -> repository.getAudiobookByIdFlow(t.audiobookId)
        else -> flowOf<AudioBookEntity?>(null)
    }

    // Download progress observer — keyed by the same "pairId" input we pass
    // to DownloadWorker. For standalones, that key is the ebook/audiobook id.
    private val _downloadPercent = MutableStateFlow<Int?>(null)
    private val _snack = MutableStateFlow<String?>(null)

    // Description / publisher / series info is fetched over the network
    // (not in the local cache). Null until the first request lands, or stays
    // null if the device is offline.
    private val _extendedMeta = MutableStateFlow<BookExtendedMeta?>(null)

    // ------------------------------------------------------------------
    // Combined UI state.
    // ------------------------------------------------------------------
    val uiState: StateFlow<BookDetailsUi> = combine(
        // Base flow — entities + progress + snackbar.
        combine(pairFlow, ebookFlow, audioFlow, _downloadPercent, _snack) {
            pair, ebook, audio, pct, snack ->
            BookDetailsUi(
                loading = when (target) {
                    is DetailsTarget.Pair      -> pair == null
                    is DetailsTarget.Ebook     -> ebook == null
                    is DetailsTarget.Audiobook -> audio == null
                },
                pair = pair,
                ebook = ebook,
                audiobook = audio,
                downloadPercent = pct,
                message = snack,
            )
        },
        _extendedMeta,
    ) { base, meta -> base.copy(extendedMeta = meta) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), BookDetailsUi())

    init {
        observeDownloadProgress()
        loadExtendedMetadata()
    }

    /**
     * Pull description + publisher + series metadata from the server.
     * For a pair, we fetch the ebook side (description is usually richer
     * there than on the audiobook). Failures are silent — the screen still
     * renders, just without the description.
     */
    private fun loadExtendedMetadata() {
        viewModelScope.launch {
            try {
                val resp = when (val t = target) {
                    is DetailsTarget.Pair -> {
                        // Need the pair to learn its ebook id. Wait for first emission.
                        val pair = pairFlow.filterNotNull().first()
                        api.getEbookMetadata(pair.ebookId)
                    }
                    is DetailsTarget.Ebook -> api.getEbookMetadata(t.ebookId)
                    is DetailsTarget.Audiobook -> api.getAudiobookMetadata(t.audiobookId)
                }
                _extendedMeta.value = BookExtendedMeta(
                    description = resp.description,
                    series = resp.series,
                    seriesIndex = resp.series_index,
                    publisher = resp.publisher,
                    publishYear = resp.publish_year,
                    language = resp.language,
                    narrators = resp.narrators,
                )
            } catch (_: Exception) {
                // Offline / 404 / 500 — leave meta null, screen still renders.
            }
        }
    }

    fun clearMessage() { _snack.value = null }

    private fun observeDownloadProgress() {
        val watchId = when (val t = target) {
            is DetailsTarget.Pair      -> t.pairId
            is DetailsTarget.Ebook     -> t.ebookId
            is DetailsTarget.Audiobook -> t.audiobookId
        }
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                var pct: Int? = null
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val pairId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        if (pairId == watchId) {
                            pct = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0).coerceIn(0, 100)
                        }
                    } else if (info.state == WorkInfo.State.FAILED) {
                        val pairId = info.outputData.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        if (pairId == watchId) {
                            info.outputData.getString(DownloadWorker.ERROR_KEY)?.let { _snack.value = it }
                        }
                    }
                }
                _downloadPercent.value = pct
            }
        }
    }

    // --- Download actions --------------------------------------------------

    fun downloadPair() {
        val p = (target as? DetailsTarget.Pair) ?: return
        enqueue(p.pairId, "ALL", "download_pair_${p.pairId}")
    }

    fun downloadEbookOnly() {
        val p = (target as? DetailsTarget.Pair) ?: return
        enqueue(p.pairId, "EBOOK", "download_ebook_${p.pairId}")
    }

    fun downloadAudiobookOnly() {
        val p = (target as? DetailsTarget.Pair) ?: return
        enqueue(p.pairId, "AUDIOBOOK", "download_audio_${p.pairId}")
    }

    fun refreshSyncData() {
        val p = (target as? DetailsTarget.Pair) ?: return
        viewModelScope.launch {
            try {
                repository.resetSyncMapDownloaded(p.pairId)
            } catch (_: Exception) { /* non-fatal — worker will try again */ }
            enqueue(p.pairId, "SYNC_MAP", "download_sync_${p.pairId}")
            _snack.value = "Refreshing sync data…"
        }
    }

    fun downloadStandaloneEbook() {
        val e = (target as? DetailsTarget.Ebook) ?: return
        enqueue(e.ebookId, "STANDALONE_EBOOK", "download_standalone_ebook_${e.ebookId}")
    }

    fun downloadStandaloneAudiobook() {
        val a = (target as? DetailsTarget.Audiobook) ?: return
        enqueue(a.audiobookId, "STANDALONE_AUDIOBOOK", "download_standalone_audio_${a.audiobookId}")
    }

    private fun enqueue(id: Int, type: String, uniqueName: String) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to id,
                DownloadWorker.KEY_TYPE    to type,
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
        _downloadPercent.value = 0
    }

    // --- Delete / unlink ---------------------------------------------------

    fun deleteEbook() {
        val pair = uiState.value.pair ?: return
        runSafely { repository.deleteEbook(pair) }
    }

    fun deleteAudiobook() {
        val pair = uiState.value.pair ?: return
        runSafely { repository.deleteAudiobook(pair) }
    }

    fun deleteStandaloneEbook() {
        val e = uiState.value.ebook ?: return
        runSafely { repository.deleteStandaloneEbook(e) }
    }

    fun deleteStandaloneAudiobook() {
        val a = uiState.value.audiobook ?: return
        runSafely { repository.deleteStandaloneAudiobook(a) }
    }

    fun unlinkPair() {
        val pair = uiState.value.pair ?: return
        runSafely { repository.deletePair(pair.id) }
    }

    // --- Progress / completion --------------------------------------------

    fun resetProgress() {
        val ui = uiState.value
        runSafely {
            when {
                // Paired: delete the canonical bookmark + progress server-side
                // and clear the matching local caches, rather than pinning
                // both legs at 0 with the legacy per-media write (which left
                // the old bookmark in place to silently re-seed progress).
                ui.pair != null -> repository.resetPairProgress(ui.pair.id)
                ui.ebook != null     -> repository.resetMediaProgress("ebook", ui.ebook.id)
                ui.audiobook != null -> repository.resetMediaProgress("audiobook", ui.audiobook.id)
            }
            _snack.value = "Progress reset"
        }
    }

    fun markComplete() {
        val ui = uiState.value
        runSafely {
            when {
                ui.pair != null -> {
                    repository.markComplete("audiobook", ui.pair.audiobookId)
                    repository.markComplete("ebook", ui.pair.ebookId)
                }
                ui.ebook != null     -> repository.markComplete("ebook", ui.ebook.id)
                ui.audiobook != null -> repository.markComplete("audiobook", ui.audiobook.id)
            }
            _snack.value = "Marked complete"
        }
    }

    private inline fun runSafely(crossinline block: suspend () -> Unit) {
        viewModelScope.launch {
            try { block() } catch (e: Exception) { _snack.value = e.message ?: "Action failed" }
        }
    }
}
