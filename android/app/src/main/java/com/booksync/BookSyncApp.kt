package com.booksync

import android.app.Application
import android.util.Log
import androidx.hilt.work.HiltWorkerFactory
import androidx.work.Configuration
import com.google.android.gms.cast.framework.CastContext
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

/**
 * BookSync Application class.
 * Initializes Hilt dependency injection and WorkManager with Hilt support.
 */
@HiltAndroidApp
class BookSyncApp : Application(), Configuration.Provider {

    @Inject
    lateinit var workerFactory: HiltWorkerFactory

    override fun onCreate() {
        super.onCreate()
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
