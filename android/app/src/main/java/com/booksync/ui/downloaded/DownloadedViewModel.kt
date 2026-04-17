package com.booksync.ui.downloaded

import android.content.Context
import android.os.StatFs
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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

/**
 * Storage summary shown at the top of the Downloaded tab.
 *
 * - [usedBytes] — total size of BookSync's own downloads (ebooks + audiobooks + sync maps)
 * - [totalBytes] — total capacity of the partition BookSync writes to
 * - [freeBytes] — remaining space on that partition
 */
data class StorageUsage(
    val usedBytes: Long = 0L,
    val totalBytes: Long = 0L,
    val freeBytes: Long = 0L,
) {
    /** Fraction of the partition consumed by BookSync downloads (0..1). */
    val usedFraction: Float
        get() = if (totalBytes > 0L) (usedBytes.toFloat() / totalBytes.toFloat()).coerceIn(0f, 1f) else 0f
}

@HiltViewModel
class DownloadedViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {

    private val workManager = WorkManager.getInstance(context)

    // --- Flows of currently-downloaded items ---------------------------------

    val downloadedPairs      = repository.getDownloadedPairsFlow()
    val downloadedEbooks     = repository.getDownloadedEbooksFlow()
    val downloadedAudiobooks = repository.getDownloadedAudiobooksFlow()

    // --- Download progress (pair id → percent 0..100) -----------------------

    private val _downloadingProgress = MutableStateFlow<Map<Int, Int>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    // --- Storage summary -----------------------------------------------------

    private val _storage = MutableStateFlow(StorageUsage())
    val storage = _storage.asStateFlow()

    init {
        observeWorkManager()
        refreshStorage()
    }

    /** Recompute disk usage in the background. Cheap — a few file-system calls. */
    fun refreshStorage() {
        viewModelScope.launch(Dispatchers.IO) {
            val used = listOf("ebooks", "audiobooks", "sync_maps")
                .sumOf { File(context.filesDir, it).recursiveSize() }
            val stat = runCatching { StatFs(context.filesDir.absolutePath) }.getOrNull()
            val total = stat?.let { it.blockCountLong * it.blockSizeLong } ?: 0L
            val free  = stat?.let { it.availableBlocksLong * it.blockSizeLong } ?: 0L
            _storage.value = StorageUsage(used, total, free)
        }
    }

    private fun File.recursiveSize(): Long =
        if (!exists()) 0L
        else if (isFile) length()
        else walkTopDown().filter { it.isFile }.sumOf { it.length() }

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
                // When a download finishes, disk usage changes — refresh in the background.
                if (workInfos.any { it.state == WorkInfo.State.SUCCEEDED }) refreshStorage()
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
            // Small delay lets deleteX propagate into the file system before re-reading sizes.
            delay(200)
            refreshStorage()
        }
    }

    private inline fun runSafely(crossinline block: suspend () -> Unit) {
        viewModelScope.launch {
            runCatching { block() }
            refreshStorage()
        }
    }
}
