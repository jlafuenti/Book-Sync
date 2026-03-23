package com.booksync.ui.continue_reading

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Unified item for the Continue list, wrapping pairs, standalone audiobooks, and standalone ebooks.
 */
data class ContinueItem(
    val id: String, // unique key: "pair_123", "audiobook_456", "ebook_789"
    val title: String,
    val author: String?,
    val mediaType: String, // "pair", "audiobook", "ebook"
    val pairId: Int? = null,
    val audiobookId: Int? = null,
    val ebookId: Int? = null,
    val progressPercent: Float = 0f, // 0-100
    val progressLabel: String = "",
    val updatedAt: Long = 0,
    val coverFilename: String? = null,
)

@HiltViewModel
class ContinueViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {

    private val _items = MutableStateFlow<List<ContinueItem>>(emptyList())
    val items: StateFlow<List<ContinueItem>> = _items.asStateFlow()

    init {
        // Combine all three sources
        viewModelScope.launch {
            combine(
                repository.getRecentlyPlayedPairsFlow(),
                repository.getRecentlyPlayedStandaloneAudiobooksFlow(),
                repository.getRecentlyReadEbooksFlow(),
            ) { pairs, audiobooks, ebooks ->
                val result = mutableListOf<ContinueItem>()

                for (pair in pairs) {
                    val bookmark = repository.getBookmark(pair.id)
                    val posMs = bookmark?.audioPositionMs ?: 0
                    val totalSec = pair.audiobookDurationSeconds ?: 0
                    val percent = if (totalSec > 0) (posMs / 1000f / totalSec) * 100f else 0f
                    val updatedAt = bookmark?.updatedAt?.toLongOrNull() ?: 0L

                    result.add(ContinueItem(
                        id = "pair_${pair.id}",
                        title = pair.ebookTitle ?: pair.audiobookTitle ?: "Unknown",
                        author = pair.ebookAuthor ?: pair.audiobookAuthor,
                        mediaType = "pair",
                        pairId = pair.id,
                        audiobookId = pair.audiobookId,
                        ebookId = pair.ebookId,
                        progressPercent = percent,
                        progressLabel = formatMs(posMs),
                        updatedAt = updatedAt,
                        coverFilename = pair.audiobookFilename,
                    ))
                }

                for (ab in audiobooks) {
                    val progress = repository.getProgressOnce("audiobook", ab.id)
                    val posMs = progress?.audioPositionMs ?: 0
                    val totalSec = ab.durationSeconds ?: 0
                    val percent = if (totalSec > 0) (posMs / 1000f / totalSec) * 100f else 0f

                    result.add(ContinueItem(
                        id = "audiobook_${ab.id}",
                        title = ab.title,
                        author = ab.author,
                        mediaType = "audiobook",
                        audiobookId = ab.id,
                        progressPercent = percent,
                        progressLabel = formatMs(posMs),
                        updatedAt = progress?.updatedAt ?: 0L,
                        coverFilename = ab.coverFilename,
                    ))
                }

                for (eb in ebooks) {
                    val progress = repository.getProgressOnce("ebook", eb.id)
                    val percent = progress?.epubProgressPercent ?: 0f

                    result.add(ContinueItem(
                        id = "ebook_${eb.id}",
                        title = eb.title,
                        author = eb.author,
                        mediaType = "ebook",
                        ebookId = eb.id,
                        progressPercent = percent,
                        progressLabel = "${percent.toInt()}%",
                        updatedAt = progress?.updatedAt ?: 0L,
                    ))
                }

                // Sort by most recently updated
                result.sortByDescending { it.updatedAt }
                result
            }.collect { _items.value = it }
        }
    }

    fun markComplete(item: ContinueItem) {
        viewModelScope.launch {
            when (item.mediaType) {
                "pair" -> {
                    item.audiobookId?.let { repository.markComplete("audiobook", it) }
                    item.ebookId?.let { repository.markComplete("ebook", it) }
                }
                "audiobook" -> item.audiobookId?.let { repository.markComplete("audiobook", it) }
                "ebook" -> item.ebookId?.let { repository.markComplete("ebook", it) }
            }
        }
    }

    fun resetProgress(item: ContinueItem) {
        viewModelScope.launch {
            when (item.mediaType) {
                "pair" -> {
                    item.audiobookId?.let { repository.resetMediaProgress("audiobook", it) }
                    item.ebookId?.let { repository.resetMediaProgress("ebook", it) }
                }
                "audiobook" -> item.audiobookId?.let { repository.resetMediaProgress("audiobook", it) }
                "ebook" -> item.ebookId?.let { repository.resetMediaProgress("ebook", it) }
            }
        }
    }

    private fun formatMs(ms: Int): String {
        val totalSec = ms / 1000
        val h = totalSec / 3600
        val m = (totalSec % 3600) / 60
        val s = totalSec % 60
        return if (h > 0) "$h:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}"
        else "$m:${s.toString().padStart(2, '0')}"
    }
}
