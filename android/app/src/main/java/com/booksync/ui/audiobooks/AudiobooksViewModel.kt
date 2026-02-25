package com.booksync.ui.audiobooks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.AudioBookEntity
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

    fun downloadAudiobook(audiobook: AudioBookEntity) {
        viewModelScope.launch {
            _downloadingProgress.value = _downloadingProgress.value + (audiobook.id to "Starting download...")
            try {
                if (!audiobook.isDownloaded) {
                    repository.downloadStandaloneAudiobook(audiobook) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (audiobook.id to "Downloading Audiobook ($p%)...")
                    }
                }
            } catch (_: Exception) {}
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
