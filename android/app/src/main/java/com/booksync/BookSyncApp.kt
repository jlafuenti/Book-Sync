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
import com.booksync.diagnostics.DiagnosticLogger
import com.google.android.gms.cast.framework.CastContext
import dagger.hilt.android.HiltAndroidApp
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
