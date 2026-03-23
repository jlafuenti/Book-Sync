package com.booksync.ui.downloaded

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.*
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class DownloadedViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {
    private val workManager = WorkManager.getInstance(context)

    val downloadedPairs = repository.getDownloadedPairsFlow()
    val downloadedEbooks = repository.getDownloadedEbooksFlow()
    val downloadedAudiobooks = repository.getDownloadedAudiobooksFlow()

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    init {
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                val newProgress = mutableMapOf<Int, String>()
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val pairId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        if (pairId != -1) {
                            val progress = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                            val currentType = info.progress.getString("CURRENT")
                            val typeLabel = when (currentType) {
                                "EBOOK" -> "Ebook"
                                "AUDIOBOOK" -> "Audiobook"
                                "SYNC_MAP" -> "Sync Data"
                                else -> "files"
                            }
                            if (progress >= 0) {
                                newProgress[pairId] = "Downloading $typeLabel ($progress%)..."
                            } else {
                                newProgress[pairId] = "Downloading $typeLabel..."
                            }
                        }
                    }
                }
                _downloadingProgress.value = newProgress
            }
        }
    }

    fun downloadAll(pair: BookPairEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "ALL"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "download_pair_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting download...")
    }

    fun downloadEbookOnly(pair: BookPairEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "EBOOK"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "download_ebook_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Ebook download...")
    }

    fun downloadAudiobookOnly(pair: BookPairEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "AUDIOBOOK"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "download_audio_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Audiobook download...")
    }

    fun deleteEbook(pair: BookPairEntity) = viewModelScope.launch { repository.deleteEbook(pair) }
    fun deleteAudiobook(pair: BookPairEntity) = viewModelScope.launch { repository.deleteAudiobook(pair) }
    fun deleteStandaloneEbook(ebook: EBookEntity) = viewModelScope.launch { repository.deleteStandaloneEbook(ebook) }
    fun deleteStandaloneAudiobook(audio: AudioBookEntity) = viewModelScope.launch { repository.deleteStandaloneAudiobook(audio) }

    fun refreshSyncData(pair: BookPairEntity) {
        viewModelScope.launch {
            repository.resetSyncMapDownloaded(pair.id)
        }
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "SYNC_MAP"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "sync_map_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Refreshing sync data...")
    }

    fun markComplete(pair: BookPairEntity) {
        viewModelScope.launch {
            pair.audiobookId?.let { repository.markComplete("audiobook", it) }
            pair.ebookId?.let { repository.markComplete("ebook", it) }
        }
    }

    fun resetProgress(pair: BookPairEntity) {
        viewModelScope.launch {
            pair.audiobookId?.let { repository.resetMediaProgress("audiobook", it) }
            pair.ebookId?.let { repository.resetMediaProgress("ebook", it) }
        }
    }
}
