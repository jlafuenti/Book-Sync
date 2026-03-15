package com.booksync

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.google.android.gms.cast.framework.CastContext
import dagger.hilt.android.AndroidEntryPoint
import com.booksync.ui.BookSyncNavigation
import com.booksync.ui.theme.BookSyncTheme

/**
 * Main entry point activity for the BookSync app.
 */
@AndroidEntryPoint
class MainActivity : AppCompatActivity() {

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { _: Boolean ->
        // We just request it so that WorkManager download notifications can appear
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Initialize Cast SDK eagerly so it's ready when the player screen appears.
        // Wrapped in try/catch because Cast is unavailable on some devices (e.g. Amazon Fire).
        try {
            CastContext.getSharedInstance(this)
        } catch (_: Exception) {}

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        setContent {
            BookSyncTheme {
                BookSyncNavigation()
            }
        }
    }
}
