package com.booksync.ui.ebooks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

@HiltViewModel
class EbooksViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    val ebooks = repository.getEbooksFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    private val _unpairedAudiobooks = MutableStateFlow<List<AudioBookEntity>>(emptyList())
    val unpairedAudiobooks = _unpairedAudiobooks.asStateFlow()

    private val _pairingError = MutableStateFlow<String?>(null)
    val pairingError = _pairingError.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                repository.refreshEbooks()
            } catch (_: Exception) {}
            _refreshing.value = false
        }
    }

    fun loadUnpairedAudiobooks() {
        viewModelScope.launch {
            try {
                _unpairedAudiobooks.value = repository.getUnpairedAudiobooks()
            } catch (_: Exception) {}
        }
    }

    fun pairWithAudiobook(ebookId: Int, audiobookId: Int) {
        viewModelScope.launch {
            try {
                repository.createPair(ebookId, audiobookId)
                _pairingError.value = null
                refresh()
            } catch (e: Exception) {
                _pairingError.value = e.message
            }
        }
    }

    fun clearPairingError() {
        _pairingError.value = null
    }

    fun downloadEbook(ebook: EBookEntity) {
        viewModelScope.launch {
            _downloadingProgress.value = _downloadingProgress.value + (ebook.id to "Starting download...")
            try {
                if (!ebook.isDownloaded) {
                    repository.downloadStandaloneEbook(ebook) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (ebook.id to "Downloading Ebook ($p%)...")
                    }
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - ebook.id
        }
    }

    fun deleteEbook(ebook: EBookEntity) {
        viewModelScope.launch {
            try {
                repository.deleteStandaloneEbook(ebook)
            } catch (_: Exception) {}
        }
    }
}
