package com.booksync.ui.series

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Represents a single item within a series group.
 * Can be a paired book, standalone ebook, or standalone audiobook.
 */
data class SeriesItem(
    val id: String,
    val title: String,
    val author: String?,
    val seriesIndex: Float?,
    val hasEbook: Boolean,
    val hasAudiobook: Boolean,
    val ebookFormat: String? = null,
    val audiobookFormat: String? = null,
    val isPaired: Boolean = false,
    val pairId: Int? = null,
    val ebookId: Int? = null,
    val audiobookId: Int? = null,
    val ebookDownloaded: Boolean = false,
    val audiobookDownloaded: Boolean = false,
    val status: String? = null,
)

/**
 * A group of items belonging to the same series.
 */
data class SeriesGroup(
    val name: String,
    val author: String?,
    val items: List<SeriesItem>,
)

enum class SeriesSort { NAME, COUNT }

@HiltViewModel
class SeriesViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _searchQuery = MutableStateFlow("")
    val searchQuery = _searchQuery.asStateFlow()

    private val _sortBy = MutableStateFlow(SeriesSort.NAME)
    val sortBy = _sortBy.asStateFlow()

    // Combine all three data sources
    val seriesData = combine(
        repository.getPairsFlow(),
        repository.getEbooksFlow(),
        repository.getAudiobooksFlow(),
    ) { pairs, ebooks, audiobooks ->
        buildSeriesGroups(pairs, ebooks, audiobooks)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), Pair(emptyList(), emptyList()))

    init { refresh() }

    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                // Refresh all three
                repository.refreshPairs()
                repository.refreshEbooks()
                repository.refreshAudiobooks()
            } catch (_: Exception) {}
            _refreshing.value = false
        }
    }

    fun updateSearch(query: String) { _searchQuery.value = query }
    fun updateSort(sort: SeriesSort) { _sortBy.value = sort }

    // ========== Manage Actions ==========

    private val _downloadProgress = MutableStateFlow<Map<String, String>>(emptyMap())
    val downloadProgress = _downloadProgress.asStateFlow()

    // Pairing
    private val _unpairedAudiobooks = MutableStateFlow<List<AudioBookEntity>>(emptyList())
    val unpairedAudiobooks = _unpairedAudiobooks.asStateFlow()

    private val _unpairedEbooks = MutableStateFlow<List<EBookEntity>>(emptyList())
    val unpairedEbooks = _unpairedEbooks.asStateFlow()

    private val _pairingError = MutableStateFlow<String?>(null)
    val pairingError = _pairingError.asStateFlow()

    fun loadUnpairedAudiobooks() {
        viewModelScope.launch {
            try { _unpairedAudiobooks.value = repository.getUnpairedAudiobooks() }
            catch (_: Exception) {}
        }
    }

    fun loadUnpairedEbooks() {
        viewModelScope.launch {
            try { _unpairedEbooks.value = repository.getUnpairedEbooks() }
            catch (_: Exception) {}
        }
    }

    fun createPair(ebookId: Int, audiobookId: Int) {
        viewModelScope.launch {
            try {
                repository.createPair(ebookId, audiobookId)
                _pairingError.value = null
                refresh()
            } catch (e: Exception) { _pairingError.value = e.message }
        }
    }

    fun clearPairingError() { _pairingError.value = null }

    // Pair actions
    fun downloadPairEbook(pairId: Int) {
        viewModelScope.launch {
            val pair = repository.getPairById(pairId) ?: return@launch
            _downloadProgress.value += ("pair_$pairId" to "Downloading Ebook...")
            try {
                repository.downloadEbook(pair) { p ->
                    _downloadProgress.value += ("pair_$pairId" to "Downloading Ebook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadProgress.value -= "pair_$pairId"
        }
    }

    fun downloadPairAudiobook(pairId: Int) {
        viewModelScope.launch {
            val pair = repository.getPairById(pairId) ?: return@launch
            _downloadProgress.value += ("pair_$pairId" to "Downloading Audiobook...")
            try {
                repository.downloadAudiobook(pair) { p ->
                    _downloadProgress.value += ("pair_$pairId" to "Downloading Audiobook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadProgress.value -= "pair_$pairId"
        }
    }

    fun deletePairEbook(pairId: Int) {
        viewModelScope.launch {
            val pair = repository.getPairById(pairId) ?: return@launch
            try { repository.deleteEbook(pair) } catch (_: Exception) {}
        }
    }

    fun deletePairAudiobook(pairId: Int) {
        viewModelScope.launch {
            val pair = repository.getPairById(pairId) ?: return@launch
            try { repository.deleteAudiobook(pair) } catch (_: Exception) {}
        }
    }

    fun unlinkPair(pairId: Int) {
        viewModelScope.launch {
            try {
                repository.deletePair(pairId)
                refresh()
            } catch (_: Exception) {}
        }
    }

    // Standalone ebook actions
    fun downloadStandaloneEbook(ebookId: Int) {
        viewModelScope.launch {
            val ebook = repository.getEbookById(ebookId) ?: return@launch
            _downloadProgress.value += ("ebook_$ebookId" to "Downloading Ebook...")
            try {
                repository.downloadStandaloneEbook(ebook) { p ->
                    _downloadProgress.value += ("ebook_$ebookId" to "Downloading Ebook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadProgress.value -= "ebook_$ebookId"
        }
    }

    fun deleteStandaloneEbook(ebookId: Int) {
        viewModelScope.launch {
            val ebook = repository.getEbookById(ebookId) ?: return@launch
            try { repository.deleteStandaloneEbook(ebook) } catch (_: Exception) {}
        }
    }

    // Standalone audiobook actions
    fun downloadStandaloneAudiobook(audiobookId: Int) {
        viewModelScope.launch {
            val audio = repository.getAudiobookById(audiobookId) ?: return@launch
            _downloadProgress.value += ("audio_$audiobookId" to "Downloading Audiobook...")
            try {
                repository.downloadStandaloneAudiobook(audio) { p ->
                    _downloadProgress.value += ("audio_$audiobookId" to "Downloading Audiobook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadProgress.value -= "audio_$audiobookId"
        }
    }

    fun deleteStandaloneAudiobook(audiobookId: Int) {
        viewModelScope.launch {
            val audio = repository.getAudiobookById(audiobookId) ?: return@launch
            try { repository.deleteStandaloneAudiobook(audio) } catch (_: Exception) {}
        }
    }

    private fun buildSeriesGroups(
        pairs: List<BookPairEntity>,
        ebooks: List<EBookEntity>,
        audiobooks: List<AudioBookEntity>,
    ): Pair<List<SeriesGroup>, List<SeriesItem>> {
        val pairedEbookIds = pairs.map { it.ebookId }.toSet()
        val pairedAudiobookIds = pairs.map { it.audiobookId }.toSet()
        val allItems = mutableListOf<SeriesItem>()

        // Pairs — get series from the corresponding ebook/audiobook entities
        pairs.forEach { p ->
            val ebook = ebooks.find { it.id == p.ebookId }
            val audio = audiobooks.find { it.id == p.audiobookId }
            val series = ebook?.series ?: audio?.series
            val seriesIndex = ebook?.seriesIndex ?: audio?.seriesIndex
            allItems.add(
                SeriesItem(
                    id = "pair_${p.id}",
                    title = p.ebookTitle,
                    author = p.ebookAuthor ?: p.audiobookAuthor,
                    seriesIndex = seriesIndex,
                    hasEbook = true, hasAudiobook = true,
                    ebookFormat = p.ebookFormat,
                    audiobookFormat = p.audiobookFormat,
                    isPaired = true, pairId = p.id,
                    ebookId = p.ebookId, audiobookId = p.audiobookId,
                    ebookDownloaded = p.ebookDownloaded,
                    audiobookDownloaded = p.audiobookDownloaded,
                    status = p.status,
                )
            )
        }

        // Standalone ebooks
        ebooks.filter { it.id !in pairedEbookIds }.forEach { e ->
            allItems.add(
                SeriesItem(
                    id = "ebook_${e.id}",
                    title = e.title, author = e.author,
                    seriesIndex = e.seriesIndex,
                    hasEbook = true, hasAudiobook = false,
                    ebookFormat = e.format, ebookId = e.id,
                    ebookDownloaded = e.isDownloaded,
                )
            )
        }

        // Standalone audiobooks
        audiobooks.filter { it.id !in pairedAudiobookIds }.forEach { a ->
            allItems.add(
                SeriesItem(
                    id = "audio_${a.id}",
                    title = a.title, author = a.author,
                    seriesIndex = a.seriesIndex,
                    hasEbook = false, hasAudiobook = true,
                    audiobookFormat = a.format, audiobookId = a.id,
                    audiobookDownloaded = a.isDownloaded,
                )
            )
        }

        // Separate items with series from those without
        val (withSeries, withoutSeries) = allItems.partition {
            // Get series from the ebook/audiobook entities
            val ebook = ebooks.find { e -> e.id == it.ebookId }
            val audio = audiobooks.find { a -> a.id == it.audiobookId }
            val series = ebook?.series ?: audio?.series
            !series.isNullOrBlank()
        }

        // Group
        val groups = withSeries.groupBy { item ->
            val ebook = ebooks.find { e -> e.id == item.ebookId }
            val audio = audiobooks.find { a -> a.id == item.audiobookId }
            ebook?.series ?: audio?.series ?: ""
        }.map { (seriesName, items) ->
            SeriesGroup(
                name = seriesName,
                author = items.firstNotNullOfOrNull { it.author },
                items = items.sortedBy { it.seriesIndex ?: 999f },
            )
        }

        return Pair(groups, withoutSeries)
    }
}
