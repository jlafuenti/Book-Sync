package com.booksync

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import android.util.Log
import androidx.hilt.work.HiltWorkerFactory
import androidx.work.Configuration
import coil.Coil
import coil.ImageLoader
import com.booksync.data.sync.SyncWorker
import com.booksync.data.util.NetworkMonitor
import com.booksync.diagnostics.DiagnosticLogger
import com.google.android.gms.cast.framework.CastContext
import dagger.hilt.android.HiltAndroidApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import javax.inject.Inject

/**
 * BookSync Application class.
 * Initializes Hilt dependency injection and WorkManager with Hilt support.
 */
@HiltAndroidApp
class BookSyncApp : Application(), Configuration.Provider {

    companion object {
        const val DIAG_CHANNEL_ID = "booksync_diagnostics"
    }

    @Inject
    lateinit var workerFactory: HiltWorkerFactory

    @Inject
    lateinit var diagnosticLogger: DiagnosticLogger

    /** Same OkHttpClient used by Retrofit — carries AuthInterceptor so Coil can fetch
     *  authenticated cover images from /api/files/covers/{filename}. */
    @Inject
    lateinit var okHttpClient: OkHttpClient

    @Inject
    lateinit var networkMonitor: NetworkMonitor

    /** Lives as long as the process — this observer must outlive every screen. */
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        // Clear any leftover "until app closed" diagnostic session from a previous run.
        diagnosticLogger.clearAppCloseMode()

        // Wire Coil with the same auth-capable OkHttpClient that Retrofit uses.
        // This lets AsyncImage fetch /api/files/covers/{filename} with bearer tokens.
        Coil.setImageLoader {
            ImageLoader.Builder(this)
                .okHttpClient(okHttpClient)
                .crossfade(true)
                .build()
        }

        // Create the notification channel for diagnostic status notifications (API 26+).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                DIAG_CHANNEL_ID,
                "Android Auto Diagnostics",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Shown while diagnostic log collection is active"
                setShowBadge(false)
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }

        // Drain the offline write queue (`pending_sync`).
        //
        // Neither scheduler was called before, so nothing ever ran SyncWorker:
        // the queue only drained if the user happened to open the Library
        // screen (LibraryViewModel's own processPendingSync call). Reconnecting
        // did not flush it, and neither did relaunching — verified on a device,
        // where 366 writes from a morning's listening away from the LAN sat
        // queued until the Library tab was opened.
        SyncWorker.enqueuePeriodicSync(this)

        // ...and drain as soon as the device is actually reachable again, which
        // is the case the periodic run handles badly: getting home mid-session
        // would otherwise wait up to 15 minutes.
        //
        // `isOnline` is a StateFlow, so this also fires once at startup when
        // already online. That is intentional — but it means two runs can
        // overlap, which is why `processPendingSync` holds a mutex. Enqueuing
        // both unconditionally at startup without that guard made the two
        // workers replay the same 366-row queue twice over.
        appScope.launch {
            // StateFlow already conflates duplicates, so this yields the
            // current value (if online) and then each offline -> online edge.
            networkMonitor.isOnline
                .filter { online -> online }
                .collect { SyncWorker.triggerImmediateSync(this@BookSyncApp) }
        }

        // Initialize Cast SDK here so it's ready whether the app is launched by the user
        // or by Android Auto starting the media service directly.
        // Wrapped in try/catch because Cast is unavailable on some devices (e.g. Amazon Fire).
        try {
            CastContext.getSharedInstance(this)
        } catch (e: Exception) {
            Log.d("BookSyncApp", "Cast not available: ${e.message}")
        }
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder()
            .setWorkerFactory(workerFactory)
            .build()
}
