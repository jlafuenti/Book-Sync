package com.booksync.ui.downloaded

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class DownloadedViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    serverUrlManager: com.booksync.data.remote.ServerUrlManager,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {

    private val workManager = WorkManager.getInstance(context)

    val serverUrl: String = serverUrlManager.currentUrl

    // --- Flows of currently-downloaded items ---------------------------------

    val downloadedPairs      = repository.getDownloadedPairsFlow()
    val downloadedEbooks     = repository.getDownloadedEbooksFlow()
    val downloadedAudiobooks = repository.getDownloadedAudiobooksFlow()

    // --- Download progress (pair id → percent 0..100) -----------------------

    private val _downloadingProgress = MutableStateFlow<Map<Int, Int>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    init {
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                val progress = mutableMapOf<Int, Int>()
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val pairId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        val pct = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                        if (pairId != -1) progress[pairId] = pct.coerceIn(0, 100)
                    }
                }
                _downloadingProgress.value = progress
            }
        }
    }

    // --- Actions -------------------------------------------------------------

    fun downloadAll(pair: BookPairEntity)           = enqueue(pair.id, "ALL",       "download_pair_${pair.id}")
    fun downloadEbookOnly(pair: BookPairEntity)     = enqueue(pair.id, "EBOOK",     "download_ebook_${pair.id}")
    fun downloadAudiobookOnly(pair: BookPairEntity) = enqueue(pair.id, "AUDIOBOOK", "download_audio_${pair.id}")

    fun refreshSyncData(pair: BookPairEntity) {
        viewModelScope.launch { repository.resetSyncMapDownloaded(pair.id) }
        enqueue(pair.id, "SYNC_MAP", "sync_map_${pair.id}")
    }

    private fun enqueue(pairId: Int, type: String, uniqueName: String) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pairId,
                DownloadWorker.KEY_TYPE    to type,
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
        _downloadingProgress.value = _downloadingProgress.value + (pairId to 0)
    }

    fun deleteEbook(pair: BookPairEntity)                    = runSafely { repository.deleteEbook(pair) }
    fun deleteAudiobook(pair: BookPairEntity)                = runSafely { repository.deleteAudiobook(pair) }
    fun deleteStandaloneEbook(ebook: EBookEntity)            = runSafely { repository.deleteStandaloneEbook(ebook) }
    fun deleteStandaloneAudiobook(audio: AudioBookEntity)    = runSafely { repository.deleteStandaloneAudiobook(audio) }

    fun markComplete(pair: BookPairEntity) = runSafely {
        repository.markComplete("audiobook", pair.audiobookId)
        repository.markComplete("ebook", pair.ebookId)
    }

    fun resetProgress(pair: BookPairEntity) = runSafely {
        repository.resetMediaProgress("audiobook", pair.audiobookId)
        repository.resetMediaProgress("ebook", pair.ebookId)
    }

    fun unlinkPair(pair: BookPairEntity) = runSafely { repository.deletePair(pair.id) }

    /**
     * Wipe every local file. Used by Account → "Clear all downloads".
     * Iterates the three current snapshots; deletions cascade through the repository's
     * usual offline-safe paths.
     */
    fun clearAllDownloads() {
        viewModelScope.launch {
            runCatching {
                downloadedPairs.first().forEach {
                    repository.deleteEbook(it)
                    repository.deleteAudiobook(it)
                }
                downloadedEbooks.first().forEach { repository.deleteStandaloneEbook(it) }
                downloadedAudiobooks.first().forEach { repository.deleteStandaloneAudiobook(it) }
            }
        }
    }

    private inline fun runSafely(crossinline block: suspend () -> Unit) {
        viewModelScope.launch {
            runCatching { block() }
        }
    }
}
