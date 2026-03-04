package com.booksync.ui.ebooks

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
import java.io.File
import javax.inject.Inject

@HiltViewModel
class EbooksViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {
    private val workManager = WorkManager.getInstance(context)

    val ebooks = repository.getEbooksFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    private val _downloadError = MutableStateFlow<String?>(null)
    val downloadError = _downloadError.asStateFlow()

    private val _unpairedAudiobooks = MutableStateFlow<List<AudioBookEntity>>(emptyList())
    val unpairedAudiobooks = _unpairedAudiobooks.asStateFlow()

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
                        val ebookId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        val workType = info.progress.getString(DownloadWorker.KEY_TYPE)
                        if (ebookId != -1 && workType == "STANDALONE_EBOOK") {
                            val progress = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                            val typeLabel = "Ebook"
                            if (progress >= 0) {
                                newProgress[ebookId] = "Downloading $typeLabel ($progress%)..."
                            } else {
                                newProgress[ebookId] = "Downloading $typeLabel..."
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

    fun clearDownloadError() {
        _downloadError.value = null
    }

    fun downloadEbook(ebook: EBookEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to ebook.id, // For standalone, we pass the ebook ID directly
                DownloadWorker.KEY_TYPE to "STANDALONE_EBOOK"
            ))
            .build()
        workManager.enqueueUniqueWork(
            "download_standalone_ebook_${ebook.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        // Set placeholder progress message since UI currently observes StateFlow
        _downloadingProgress.value = _downloadingProgress.value + (ebook.id to "Downloading in background...")
    }

    fun deleteEbook(ebook: EBookEntity) {
        viewModelScope.launch {
            try {
                repository.deleteStandaloneEbook(ebook)
            } catch (_: Exception) {}
        }
    }
}
