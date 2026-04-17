package com.booksync.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TranscriptionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
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
) : ViewModel() {

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
}
