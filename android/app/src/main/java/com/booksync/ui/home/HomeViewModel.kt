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
import com.booksync.data.repository.PairOpenTarget
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
    val ebookDownloaded: Boolean = false,
    val audiobookDownloaded: Boolean = false,
    val series: String? = null,
    val seriesIndex: Float? = null,
) {
    enum class MediaType { PAIR, EBOOK, AUDIOBOOK }
}

/** One row in the "In Queue" section — thin wrapper so Phase G can wire without changing the screen. */
data class HomeQueueItem(
    val pairId: Int,
    val title: String,
    val status: QueueStatus,
    val percent: Int,
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

                for (pair in pairs) {
                    val bookmark = repository.getBookmark(pair.id)
                    val posMs = bookmark?.audioPositionMs ?: 0
                    val totalSec = pair.audiobookDurationSeconds ?: 0
                    val percent = if (totalSec > 0) (posMs / 1000f / totalSec) * 100f else 0f
                    val updatedAt = bookmark?.updatedAt?.toLongOrNull() ?: 0L
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
                        updatedAtMs = progress?.updatedAt ?: 0L,
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
                        updatedAtMs = progress?.updatedAt ?: 0L,
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
        // Collect the queue polling flow and map server DTOs to HomeQueueItem.
        viewModelScope.launch {
            transcriptionRepository.activeQueueItemsFlow().collect { items ->
                _queueItems.value = items.map { item ->
                    val status = TranscriptionStatus.fromQueueItem(item)
                    HomeQueueItem(
                        pairId   = item.book_pair_id,
                        title    = item.book_title ?: "Untitled",
                        status   = when (status) {
                            is TranscriptionStatus.Transcribing -> HomeQueueItem.QueueStatus.TRANSCRIBING
                            else                                -> HomeQueueItem.QueueStatus.QUEUED
                        },
                        percent  = (status as? TranscriptionStatus.Transcribing)?.progressPercent ?: 0,
                    )
                }
            }
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
        repository.resolvePairOpenTarget(pair)

    suspend fun resolvePairOpenTarget(pairId: Int): PairOpenTarget =
        repository.resolvePairOpenTarget(pairId)

    fun downloadBoth(pair: BookPairEntity)         = enqueue(pair.id, "ALL", "download_pair_${pair.id}")
    fun downloadBothById(pairId: Int)              = enqueue(pairId,  "ALL", "download_pair_$pairId")
    fun downloadEbook(pair: BookPairEntity)     = enqueue(pair.id, "EBOOK",     "download_ebook_${pair.id}")
    fun downloadAudiobook(pair: BookPairEntity) = enqueue(pair.id, "AUDIOBOOK", "download_audio_${pair.id}")
    fun refreshSyncData(pair: BookPairEntity)   = enqueue(pair.id, "SYNC_MAP",  "download_sync_${pair.id}")

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
