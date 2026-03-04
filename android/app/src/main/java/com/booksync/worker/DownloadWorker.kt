package com.booksync.worker

import android.content.Context
import androidx.hilt.work.HiltWorker
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.booksync.data.repository.BookSyncRepository
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CancellationException

/**
 * Background worker for downloading ebooks and audiobooks.
 * Survives app backgrounding and process death.
 */
@HiltWorker
class DownloadWorker @AssistedInject constructor(
    @Assisted private val appContext: Context,
    @Assisted private val workerParams: WorkerParameters,
    private val repository: BookSyncRepository
) : CoroutineWorker(appContext, workerParams) {

    private val notificationManager = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    companion object {
        const val KEY_PAIR_ID = "PAIR_ID"
        const val KEY_TYPE = "TYPE" // "EBOOK" or "AUDIOBOOK" or "ALL"
        
        const val PROGRESS_KEY = "PROGRESS"
        const val ERROR_KEY = "ERROR"
    }

    private var notificationBuilder: NotificationCompat.Builder? = null
    private val notificationId = 1994

    private fun updateNotificationProgress(progress: Int, typeText: String) {
        notificationBuilder?.let { builder ->
            if (progress >= 0) {
                builder.setContentText("$typeText: $progress%")
                    .setProgress(100, progress, false)
            } else {
                builder.setContentText("Downloading $typeText...")
                    .setProgress(100, 0, true)
            }
            notificationManager.notify(notificationId, builder.build())
        }
    }

    override suspend fun doWork(): Result {
        val pairId = inputData.getInt(KEY_PAIR_ID, -1)
        val type = inputData.getString(KEY_TYPE) ?: "ALL"

        if (pairId == -1) return Result.failure(workDataOf(ERROR_KEY to "Invalid pair ID"))

        return withContext(Dispatchers.IO) {
            try {
                Log.d("DownloadWorker", "Starting doWork for pairId=$pairId, type=$type")

                if (type == "STANDALONE_EBOOK") {
                    val ebook = repository.getEbookById(pairId)
                        ?: return@withContext Result.failure(workDataOf(ERROR_KEY to "Ebook not found in DB"))
                    
                    try {
                        val info = createForegroundInfo(type, ebook.title)
                        setForeground(info)
                    } catch (e: Exception) {
                        Log.e("DownloadWorker", "setForeground() failed! Exception:", e)
                    }

                    if (!ebook.isDownloaded) {
                        repository.downloadStandaloneEbook(ebook) { progress ->
                            setProgressAsync(workDataOf(PROGRESS_KEY to progress, "CURRENT" to "EBOOK", KEY_PAIR_ID to pairId, KEY_TYPE to type))
                            updateNotificationProgress(progress, "Ebook")
                        }
                    }
                    return@withContext Result.success()
                }

                if (type == "STANDALONE_AUDIOBOOK") {
                    val audiobook = repository.getAudiobookById(pairId)
                        ?: return@withContext Result.failure(workDataOf(ERROR_KEY to "Audiobook not found in DB"))
                    
                    try {
                        val info = createForegroundInfo(type, audiobook.title)
                        setForeground(info)
                    } catch (e: Exception) {
                        Log.e("DownloadWorker", "setForeground() failed! Exception:", e)
                    }

                    if (!audiobook.isDownloaded) {
                        repository.downloadStandaloneAudiobook(audiobook) { progress ->
                            setProgressAsync(workDataOf(PROGRESS_KEY to progress, "CURRENT" to "AUDIOBOOK", KEY_PAIR_ID to pairId, KEY_TYPE to type))
                            updateNotificationProgress(progress, "Audiobook")
                        }
                    }
                    return@withContext Result.success()
                }

                // Otherwise, it's a BookPair
                val pair = repository.getPairById(pairId)
                    ?: return@withContext Result.failure(workDataOf(ERROR_KEY to "Pair not found in DB"))

                try {
                    val info = createForegroundInfo(type, pair.ebookTitle)
                    setForeground(info)
                } catch (e: Exception) {
                    Log.e("DownloadWorker", "setForeground() failed! Exception:", e)
                }

                if (type == "EBOOK" || type == "ALL") {
                    if (!pair.ebookDownloaded) {
                        repository.downloadEbook(pair) { progress ->
                            setProgressAsync(workDataOf(PROGRESS_KEY to progress, "CURRENT" to "EBOOK", KEY_PAIR_ID to pairId, KEY_TYPE to type))
                            updateNotificationProgress(progress, "Ebook")
                        }
                    }
                }

                if (type == "AUDIOBOOK" || type == "ALL") {
                    if (!pair.audiobookDownloaded) {
                        repository.downloadAudiobook(pair) { progress ->
                            setProgressAsync(workDataOf(PROGRESS_KEY to progress, "CURRENT" to "AUDIOBOOK", KEY_PAIR_ID to pairId, KEY_TYPE to type))
                            updateNotificationProgress(progress, "Audiobook")
                        }
                    }
                }

                if (type == "ALL" && !pair.syncMapDownloaded && pair.status == "synced") {
                    updateNotificationProgress(-1, "Sync Data")
                    setProgressAsync(workDataOf(PROGRESS_KEY to -1, "CURRENT" to "SYNC_MAP", KEY_PAIR_ID to pairId, KEY_TYPE to type))
                    repository.downloadSyncMap(pair.id)
                }

                Result.success()
            } catch (e: CancellationException) {
                Log.w("DownloadWorker", "Worker was cancelled!", e)
                throw e // MUST throw CancellationException so WorkManager handles cancellation correctly
            } catch (e: Exception) {
                Log.e("DownloadWorker", "Worker failed with exception", e)
                Result.failure(workDataOf(ERROR_KEY to (e.message ?: "Unknown download error")))
            }
        }
    }

    override suspend fun getForegroundInfo(): androidx.work.ForegroundInfo {
        // As a fallback during fast initialization, we use a generic title
        val type = inputData.getString(KEY_TYPE) ?: "ALL"
        return createForegroundInfo(type, "BookFiles")
    }

    private fun createForegroundInfo(type: String, title: String): androidx.work.ForegroundInfo {
        val id = "booksync_downloads"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                id,
                "Book Downloads",
                NotificationManager.IMPORTANCE_LOW
            )
            notificationManager.createNotificationChannel(channel)
        }

        val typeText = when (type) {
            "STANDALONE_EBOOK" -> "Ebook"
            "STANDALONE_AUDIOBOOK" -> "Audiobook"
            "EBOOK" -> "Ebook"
            "AUDIOBOOK" -> "Audiobook"
            else -> "Book files"
        }

        val builder = NotificationCompat.Builder(applicationContext, id)
            .setContentTitle("Downloading $title")
            .setContentText("Starting $typeText download...")
            .setSmallIcon(android.R.drawable.stat_sys_download) // built-in android icon
            .setOngoing(true)
            .setProgress(100, 0, true)
            
        notificationBuilder = builder
        val notification = builder.build()

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            androidx.work.ForegroundInfo(notificationId, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            androidx.work.ForegroundInfo(notificationId, notification)
        }
    }
}
