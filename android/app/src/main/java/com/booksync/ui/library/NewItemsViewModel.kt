package com.booksync.ui.library

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
 * Represents a mismatch detected in a BookPairEntity between its ebook and audiobook sides.
 */
data class PairMismatch(
    val field: String,  // e.g. "Title", "Author", "Ebook filename", "Audiobook filename"
    val ebookValue: String,
    val audiobookValue: String,
)

/** Detects metadata mismatches within a pair. */
fun BookPairEntity.detectMismatches(): List<PairMismatch> {
    val mismatches = mutableListOf<PairMismatch>()

    if (!titlesMatch(ebookTitle, audiobookTitle)) {
        mismatches += PairMismatch("Title", ebookTitle, audiobookTitle)
    }

    val eAuthor = ebookAuthor
    val aAuthor = audiobookAuthor
    if (eAuthor != null && aAuthor != null && !authorsMatch(eAuthor, aAuthor)) {
        mismatches += PairMismatch("Author", eAuthor, aAuthor)
    }

    val eFilenameTitle = filenameToTitle(ebookFilename)
    if (!titlesMatch(eFilenameTitle, ebookTitle)) {
        mismatches += PairMismatch("Ebook filename", ebookFilename, ebookTitle)
    }

    val aFilenameTitle = filenameToTitle(audiobookFilename)
    if (!titlesMatch(aFilenameTitle, audiobookTitle)) {
        mismatches += PairMismatch("Audiobook filename", audiobookFilename, audiobookTitle)
    }

    return mismatches
}

private fun filenameToTitle(filename: String): String {
    return filename
        .substringBeforeLast('.')
        .replace(Regex("[_\\-.]"), " ")
        .trim()
        .lowercase()
}

private fun normalize(s: String) = s.trim().lowercase()

private fun titlesMatch(a: String, b: String): Boolean {
    val na = normalize(a)
    val nb = normalize(b)
    // Exact match or one contains the other (handles subtitle differences)
    return na == nb || na.contains(nb) || nb.contains(na)
}

private fun authorsMatch(a: String, b: String): Boolean {
    return normalize(a) == normalize(b)
}

@HiltViewModel
class NewItemsViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {

    val newEbooks = repository.getNewEbooksFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val newAudiobooks = repository.getNewAudiobooksFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val newPairs = repository.getNewPairsFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val newItemCount = combine(
        repository.getNewEbookCountFlow(),
        repository.getNewAudiobookCountFlow(),
    ) { e, a -> e + a }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val newPairCount = repository.getNewPairCountFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    // Set of (id, type) pairs currently selected in the New Items tab
    private val _selectedItemIds = MutableStateFlow<Set<Pair<Int, String>>>(emptySet())
    val selectedItemIds = _selectedItemIds.asStateFlow()

    // Set of (id, type) pairs currently selected in the New Pairs tab
    private val _selectedPairIds = MutableStateFlow<Set<Int>>(emptySet())
    val selectedPairIds = _selectedPairIds.asStateFlow()

    // Pair IDs whose mismatches have been skipped (auto-acknowledges the pair)
    private val _skippedMismatchPairIds = MutableStateFlow<Set<Int>>(emptySet())
    val skippedMismatchPairIds = _skippedMismatchPairIds.asStateFlow()

    // ---- New Items tab selection ----

    fun toggleItemSelect(id: Int, type: String) {
        val key = id to type
        _selectedItemIds.value = if (key in _selectedItemIds.value) {
            _selectedItemIds.value - key
        } else {
            _selectedItemIds.value + key
        }
    }

    fun selectAllItems(ebooks: List<EBookEntity>, audiobooks: List<AudioBookEntity>) {
        val all = ebooks.map { it.id to "ebook" } + audiobooks.map { it.id to "audiobook" }
        _selectedItemIds.value = all.toSet()
    }

    fun clearItemSelection() {
        _selectedItemIds.value = emptySet()
    }

    fun acknowledgeSelectedItems() {
        val selected = _selectedItemIds.value
        if (selected.isEmpty()) return
        viewModelScope.launch {
            val ebookIds = selected.filter { it.second == "ebook" }.map { it.first }
            val audiobookIds = selected.filter { it.second == "audiobook" }.map { it.first }
            if (ebookIds.isNotEmpty()) repository.acknowledgeItems(ebookIds, "ebook")
            if (audiobookIds.isNotEmpty()) repository.acknowledgeItems(audiobookIds, "audiobook")
            _selectedItemIds.value = emptySet()
        }
    }

    fun acknowledgeAllItems() {
        viewModelScope.launch {
            val ebookIds = newEbooks.value.map { it.id }
            val audiobookIds = newAudiobooks.value.map { it.id }
            if (ebookIds.isNotEmpty()) repository.acknowledgeItems(ebookIds, "ebook")
            if (audiobookIds.isNotEmpty()) repository.acknowledgeItems(audiobookIds, "audiobook")
            _selectedItemIds.value = emptySet()
        }
    }

    // ---- New Pairs tab selection ----

    fun togglePairSelect(id: Int) {
        _selectedPairIds.value = if (id in _selectedPairIds.value) {
            _selectedPairIds.value - id
        } else {
            _selectedPairIds.value + id
        }
    }

    fun selectAllPairs(pairs: List<BookPairEntity>) {
        _selectedPairIds.value = pairs.map { it.id }.toSet()
    }

    fun clearPairSelection() {
        _selectedPairIds.value = emptySet()
    }

    fun acknowledgeSelectedPairs() {
        val selected = _selectedPairIds.value
        if (selected.isEmpty()) return
        viewModelScope.launch {
            repository.acknowledgeItems(selected.toList(), "pair")
            _selectedPairIds.value = emptySet()
        }
    }

    fun acknowledgeAllPairs() {
        viewModelScope.launch {
            val ids = newPairs.value.map { it.id }
            if (ids.isNotEmpty()) repository.acknowledgeItems(ids, "pair")
            _selectedPairIds.value = emptySet()
            _skippedMismatchPairIds.value = emptySet()
        }
    }

    /** Skip all detected mismatches for a pair — auto-acknowledges it immediately. */
    fun skipAllMismatches(pairId: Int) {
        _skippedMismatchPairIds.value = _skippedMismatchPairIds.value + pairId
        viewModelScope.launch {
            repository.acknowledgeItems(listOf(pairId), "pair")
            _selectedPairIds.value = _selectedPairIds.value - pairId
        }
    }
}
