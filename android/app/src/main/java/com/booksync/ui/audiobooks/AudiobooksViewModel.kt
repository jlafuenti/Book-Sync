package com.booksync.ui.audiobooks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class AudiobooksViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    val audiobooks = repository.getAudiobooksFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    private val _downloadError = MutableStateFlow<String?>(null)
    val downloadError = _downloadError.asStateFlow()

    private val _unpairedEbooks = MutableStateFlow<List<EBookEntity>>(emptyList())
    val unpairedEbooks = _unpairedEbooks.asStateFlow()

    private val _pairingError = MutableStateFlow<String?>(null)
    val pairingError = _pairingError.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                repository.refreshAudiobooks()
            } catch (_: Exception) {}
            _refreshing.value = false
        }
    }

    fun loadUnpairedEbooks() {
        viewModelScope.launch {
            try {
                _unpairedEbooks.value = repository.getUnpairedEbooks()
            } catch (_: Exception) {}
        }
    }

    fun pairWithEbook(audiobookId: Int, ebookId: Int) {
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

    fun clearDownloadError() {
        _downloadError.value = null
    }

    fun downloadAudiobook(audiobook: AudioBookEntity) {
        viewModelScope.launch {
            _downloadingProgress.value = _downloadingProgress.value + (audiobook.id to "Starting download...")
            try {
                if (!audiobook.isDownloaded) {
                    repository.downloadStandaloneAudiobook(audiobook) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (audiobook.id to "Downloading Audiobook ($p%)...")
                    }
                }
            } catch (e: Exception) {
                _downloadError.value = e.message ?: "Download failed"
            }
            _downloadingProgress.value = _downloadingProgress.value - audiobook.id
        }
    }

    fun deleteAudiobook(audiobook: AudioBookEntity) {
        viewModelScope.launch {
            try {
                repository.deleteStandaloneAudiobook(audiobook)
            } catch (_: Exception) {}
        }
    }
}
