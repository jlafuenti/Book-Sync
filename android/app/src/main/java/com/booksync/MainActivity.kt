package com.booksync

import android.Manifest
import android.app.SearchManager
import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.util.Log
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import dagger.hilt.android.AndroidEntryPoint
import com.booksync.player.AudioPlayerService
import com.booksync.ui.BookSyncNavigation
import com.booksync.ui.account.AccountViewModel
import com.booksync.ui.theme.BookSyncTheme

/**
 * Main entry point activity for the Tandem app.
 */
@AndroidEntryPoint
class MainActivity : AppCompatActivity() {

    private val settingsViewModel: AccountViewModel by viewModels()

    /**
     * Held for the lifetime of the activity rather than released inline: the
     * service resolves a play-from-search request asynchronously (it has to hit
     * Room), and releasing the controller before it answers cancels the request.
     */
    private var searchController: MediaController? = null

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { _: Boolean ->
        // We just request it so that WorkManager download notifications can appear
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        setContent {
            val appTheme by settingsViewModel.appTheme.collectAsState()
            BookSyncTheme(appTheme = appTheme) {
                BookSyncNavigation()
            }
        }

        handlePlayFromSearch(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handlePlayFromSearch(intent)
    }

    override fun onDestroy() {
        searchController?.release()
        searchController = null
        super.onDestroy()
    }

    /**
     * "Hey Google, play Bartleby on Tandem" (issue #172).
     *
     * The manifest has advertised `MEDIA_PLAY_FROM_SEARCH` since the Auto work
     * landed, but nothing handled it, so the intent just opened the app on Home
     * — a voice action that visibly does nothing is worse than one that isn't
     * offered, and voice actions are a car app quality checklist item.
     *
     * The filter stays rather than being deleted because it is the only route
     * an Assistant request can take while the app is in the foreground and the
     * service is not yet running; the session's own search route
     * (`onSearch` / `onSetMediaItems` with a search query) covers the car.
     * Both ends land in the same place: a [MediaItem] carrying only a search
     * query, which `AudioPlayerService.onSetMediaItems` resolves through
     * `autoSearch`. Nothing about matching or ranking is decided here.
     */
    private fun handlePlayFromSearch(intent: Intent?) {
        if (intent?.action != MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH) return
        // Absent extra = "play something", which autoSearch answers with the
        // most recently played book. Same contract as an empty spoken query.
        val query = intent.getStringExtra(SearchManager.QUERY).orEmpty()
        Log.i(TAG, "MEDIA_PLAY_FROM_SEARCH query='$query'")

        val token = SessionToken(
            applicationContext,
            ComponentName(applicationContext, AudioPlayerService::class.java),
        )
        val future = MediaController.Builder(applicationContext, token).buildAsync()
        future.addListener({
            val controller = runCatching { future.get() }.getOrNull()
            if (controller == null) {
                Log.w(TAG, "MEDIA_PLAY_FROM_SEARCH: could not connect to the player service")
                return@addListener
            }
            searchController?.release()
            searchController = controller
            controller.setMediaItem(
                MediaItem.Builder()
                    .setRequestMetadata(
                        MediaItem.RequestMetadata.Builder().setSearchQuery(query).build()
                    )
                    .build()
            )
            controller.prepare()
            controller.play()
        }, ContextCompat.getMainExecutor(this))
    }

    private companion object {
        const val TAG = "MainActivity"
    }
}
