package com.booksync.ui.audiobooks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.*
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class AudiobooksViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {
    private val workManager = WorkManager.getInstance(context)

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
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                val newProgress = mutableMapOf<Int, String>()
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val audiobookId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        val workType = info.progress.getString(DownloadWorker.KEY_TYPE)
                        if (audiobookId != -1 && workType == "STANDALONE_AUDIOBOOK") {
                            val progress = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                            val typeLabel = "Audiobook"
                            if (progress >= 0) {
                                newProgress[audiobookId] = "Downloading $typeLabel ($progress%)..."
                            } else {
                                newProgress[audiobookId] = "Downloading $typeLabel..."
                            }
                        }
                    }
                }
                _downloadingProgress.value = newProgress
            }
        }
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
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to audiobook.id, // For standalone, we pass the audiobook ID directly
                DownloadWorker.KEY_TYPE to "STANDALONE_AUDIOBOOK"
            ))
            .build()
        workManager.enqueueUniqueWork(
            "download_standalone_audiobook_${audiobook.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        // Set placeholder progress message since UI currently observes StateFlow
        _downloadingProgress.value = _downloadingProgress.value + (audiobook.id to "Downloading in background...")
    }

    fun deleteAudiobook(audiobook: AudioBookEntity) {
        viewModelScope.launch {
            try {
                repository.deleteStandaloneAudiobook(audiobook)
            } catch (_: Exception) {}
        }
    }
}
