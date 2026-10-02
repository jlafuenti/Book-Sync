package com.booksync.data.repository

import android.content.Context
import androidx.work.ExistingWorkPolicy
import androidx.work.WorkManager
import com.booksync.worker.DownloadWorker

/**
 * Asks for one pair's sync map to be fetched in the background (issue #786).
 *
 * The seam [LibraryLoader] uses for the missing-map sweep, so the loader stays a plain data-layer
 * class with no WorkManager in it and its tests can record the calls. The production binding is
 * [WorkManagerSyncMapFetchScheduler], provided in `di/AppModule.kt`.
 */
fun interface SyncMapFetchScheduler {
    fun schedule(pairId: Int)
}

/**
 * Enqueues the same work the sweep always did from `LibraryViewModel`: a background `SYNC_MAP`
 * download under the unique name `download_sync_<pairId>`.
 *
 * `KEEP`, not `REPLACE`: the sweep runs after every successful library refresh (app start,
 * pull-to-refresh, every queue exit), often seconds apart, and restarting a fetch already in
 * flight would only waste bytes and delay it. The type is `SYNC_MAP`, not `SYNC_MAP_EXPLICIT`, so
 * `DownloadWorker`'s Wi-Fi-only gate still applies; only a tap on "Download sync data" or
 * "Refresh sync data" bypasses it.
 *
 * Takes the [Context] rather than a [WorkManager] and resolves the instance per call, as
 * `LibraryViewModel` did, so building the singleton graph never touches WorkManager.
 */
class WorkManagerSyncMapFetchScheduler(private val context: Context) : SyncMapFetchScheduler {
    override fun schedule(pairId: Int) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "download_sync_$pairId",
            ExistingWorkPolicy.KEEP,
            DownloadWorker.request(pairId, "SYNC_MAP"),
        )
    }
}
