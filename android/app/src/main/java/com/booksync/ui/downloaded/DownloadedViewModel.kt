package com.booksync.ui.downloaded

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class DownloadedViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    val downloadedPairs = repository.getDownloadedPairsFlow()
    val downloadedEbooks = repository.getDownloadedEbooksFlow()
    val downloadedAudiobooks = repository.getDownloadedAudiobooksFlow()

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    fun downloadAll(pair: BookPairEntity) {
        viewModelScope.launch {
            _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting download...")
            try {
                if (!pair.ebookDownloaded) {
                    repository.downloadEbook(pair) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Ebook ($p%)...")
                    }
                }
                if (!pair.audiobookDownloaded) {
                    repository.downloadAudiobook(pair) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Audiobook ($p%)...")
                    }
                }
                if (!pair.syncMapDownloaded && pair.status == "synced") {
                    _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Sync Data...")
                    repository.downloadSyncMap(pair.id)
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - pair.id
        }
    }

    fun downloadEbookOnly(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Ebook download...")
                repository.downloadEbook(pair) { p ->
                    _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Ebook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - pair.id
        }
    }

    fun downloadAudiobookOnly(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Audiobook download...")
                repository.downloadAudiobook(pair) { p ->
                    _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Audiobook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - pair.id
        }
    }

    fun deleteEbook(pair: BookPairEntity) = viewModelScope.launch { repository.deleteEbook(pair) }
    fun deleteAudiobook(pair: BookPairEntity) = viewModelScope.launch { repository.deleteAudiobook(pair) }
    fun deleteStandaloneEbook(ebook: EBookEntity) = viewModelScope.launch { repository.deleteStandaloneEbook(ebook) }
    fun deleteStandaloneAudiobook(audio: AudioBookEntity) = viewModelScope.launch { repository.deleteStandaloneAudiobook(audio) }
}
