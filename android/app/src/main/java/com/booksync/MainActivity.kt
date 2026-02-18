package com.booksync

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import dagger.hilt.android.AndroidEntryPoint
import com.booksync.ui.BookSyncNavigation
import com.booksync.ui.theme.BookSyncTheme

/**
 * Main entry point activity for the BookSync app.
 */
@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            BookSyncTheme {
                BookSyncNavigation()
            }
        }
    }
}
