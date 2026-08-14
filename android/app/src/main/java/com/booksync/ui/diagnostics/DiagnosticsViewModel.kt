package com.booksync.ui.diagnostics

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.BookSyncApp
import com.booksync.MainActivity
import com.booksync.R
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.DiagnosticsStopReceiver
import com.booksync.diagnostics.LogChannel
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

private const val ACTION_STOP_DIAGNOSTICS = "com.booksync.action.STOP_DIAGNOSTICS"

/** Returns a stable notification ID for this channel. */
fun LogChannel.notificationId() = when (this) {
    LogChannel.AUTO -> 9001
    LogChannel.APP  -> 9002
}

enum class DiagMode {
    FIVE_MINUTES,
    FIFTEEN_MINUTES,
    ONE_HOUR,
    UNTIL_APP_CLOSED,
    UNTIL_STOPPED
}

data class DiagnosticsUiState(
    val isActive: Boolean = false,
    val expiryDescription: String = "Diagnostics OFF",
    val logContent: String = ""
)

@HiltViewModel
class DiagnosticsViewModel @Inject constructor(
    private val diagnosticLogger: DiagnosticLogger,
    @param:ApplicationContext private val context: Context,
    savedStateHandle: SavedStateHandle
) : ViewModel() {

    val channel: LogChannel = savedStateHandle.get<String>("channel")
        ?.let { name -> LogChannel.entries.firstOrNull { it.name == name } }
        ?: LogChannel.AUTO

    private val _uiState = MutableStateFlow(DiagnosticsUiState())
    val uiState: StateFlow<DiagnosticsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            while (true) {
                refreshState()
                delay(1_000)
            }
        }
    }

    fun enableDiagnostics(mode: DiagMode) {
        val durationMs = when (mode) {
            DiagMode.FIVE_MINUTES    -> 5 * 60 * 1000L
            DiagMode.FIFTEEN_MINUTES -> 15 * 60 * 1000L
            DiagMode.ONE_HOUR        -> 60 * 60 * 1000L
            DiagMode.UNTIL_APP_CLOSED -> Long.MIN_VALUE
            DiagMode.UNTIL_STOPPED   -> 365 * 24 * 60 * 60 * 1000L
        }
        diagnosticLogger.enable(channel, durationMs)
        postNotification()
        refreshState()
    }

    fun disableDiagnostics() {
        diagnosticLogger.disable(channel)
        cancelNotification()
        refreshState()
    }

    fun loadLog() {
        _uiState.value = _uiState.value.copy(logContent = diagnosticLogger.readLog(channel))
    }

    fun clearLog() {
        diagnosticLogger.clearLog(channel)
        _uiState.value = _uiState.value.copy(logContent = "")
    }

    fun shareLog(onIntent: (Intent) -> Unit) {
        val file = diagnosticLogger.getLogFile(channel)
        if (!file.exists()) return

        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            file
        )
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, "${channel.label} Diagnostics")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        onIntent(Intent.createChooser(intent, "Share diagnostic log"))
    }

    // ----------------------------------------------------------------
    // Internal helpers
    // ----------------------------------------------------------------

    private fun refreshState() {
        _uiState.value = _uiState.value.copy(
            isActive = diagnosticLogger.isEnabled(channel),
            expiryDescription = diagnosticLogger.expiryDescription(channel)
        )
    }

    private fun postNotification() {
        val openIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("navigate_to", "diagnostics/${channel.name}")
        }
        val openPi = PendingIntent.getActivity(
            context, channel.notificationId(), openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(ACTION_STOP_DIAGNOSTICS).apply {
            setClass(context, DiagnosticsStopReceiver::class.java)
            putExtra("channel", channel.name)
        }
        val stopPi = PendingIntent.getBroadcast(
            context, channel.notificationId(), stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(context, BookSyncApp.DIAG_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("${channel.label} Diagnostics Active")
            .setContentText("Collecting logs — tap to stop")
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(openPi)
            .addAction(0, "Stop", stopPi)
            .build()

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(channel.notificationId(), notification)
    }

    private fun cancelNotification() {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.cancel(channel.notificationId())
    }
}
