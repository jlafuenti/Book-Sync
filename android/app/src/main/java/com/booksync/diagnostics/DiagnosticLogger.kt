package com.booksync.diagnostics

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

private const val PREFS_NAME = "booksync_diag_prefs"
private const val LOG_MAX_BYTES = 256 * 1024L  // 256 KB
private const val TAG = "DiagnosticLogger"

enum class LogChannel(
    val prefsKey: String,
    val logFileName: String,
    val label: String
) {
    AUTO("diag_auto_end_ms", "auto_diagnostics.log", "Android Auto"),
    APP("diag_app_end_ms",  "app_diagnostics.log",  "Tandem App")
}

@Singleton
class DiagnosticLogger @Inject constructor(
    @param:ApplicationContext private val context: Context
) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val fmt = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)

    private fun logFile(channel: LogChannel) = File(context.filesDir, channel.logFileName)

    // ----------------------------------------------------------------
    // Public state queries
    // ----------------------------------------------------------------

    fun isEnabled(channel: LogChannel): Boolean {
        val endMs = prefs.getLong(channel.prefsKey, 0L)
        return when {
            endMs == -1L -> true
            endMs > 0L && endMs > System.currentTimeMillis() -> true
            else -> false
        }
    }

    /** Human-readable expiry description for the UI. */
    fun expiryDescription(channel: LogChannel): String {
        val endMs = prefs.getLong(channel.prefsKey, 0L)
        return when {
            endMs == 0L -> "Diagnostics OFF"
            endMs == -1L -> "Active — until app is closed"
            else -> {
                val remaining = endMs - System.currentTimeMillis()
                if (remaining <= 0) return "Diagnostics OFF"
                val minutes = remaining / 60_000
                val seconds = (remaining % 60_000) / 1000
                when {
                    minutes >= 60 -> "Active — expires in ${minutes / 60}h ${minutes % 60}m"
                    minutes > 0  -> "Active — expires in ${minutes}m ${seconds}s"
                    else         -> "Active — expires in ${seconds}s"
                }
            }
        }
    }

    // ----------------------------------------------------------------
    // Enable / disable
    // ----------------------------------------------------------------

    /**
     * Enable diagnostics for [channel].
     * @param durationMs pass [Long.MIN_VALUE] for "until app closed" (-1L sentinel).
     */
    fun enable(channel: LogChannel, durationMs: Long) {
        val endMs = if (durationMs == Long.MIN_VALUE) -1L
                    else System.currentTimeMillis() + durationMs
        prefs.edit().putLong(channel.prefsKey, endMs).apply()
        writeRaw(channel, "=== ${channel.label} diagnostics started ===")
    }

    fun disable(channel: LogChannel) {
        writeRaw(channel, "=== ${channel.label} diagnostics stopped ===")
        prefs.edit().putLong(channel.prefsKey, 0L).apply()
    }

    /** Called by BookSyncApp.onCreate() to clear any leftover "until app closed" sessions. */
    fun clearAppCloseMode() {
        LogChannel.entries.forEach { channel ->
            if (prefs.getLong(channel.prefsKey, 0L) == -1L) {
                prefs.edit().putLong(channel.prefsKey, 0L).apply()
            }
        }
    }

    // ----------------------------------------------------------------
    // Log methods
    // ----------------------------------------------------------------

    fun i(channel: LogChannel, tag: String, msg: String) {
        Log.i(tag, msg)
        if (!isEnabled(channel)) return
        writeRaw(channel, "I/$tag: $msg")
    }

    fun w(channel: LogChannel, tag: String, msg: String) {
        Log.w(tag, msg)
        if (!isEnabled(channel)) return
        writeRaw(channel, "W/$tag: $msg")
    }

    fun e(channel: LogChannel, tag: String, msg: String, throwable: Throwable? = null) {
        Log.e(tag, msg, throwable)
        if (!isEnabled(channel)) return
        val line = if (throwable != null)
            "E/$tag: $msg — ${throwable.javaClass.simpleName}: ${throwable.message}"
        else
            "E/$tag: $msg"
        writeRaw(channel, line)
    }

    /**
     * Append a line whether or not [channel] is currently capturing (issue #364).
     *
     * Reserved for events that destroy user data and cannot be asked to happen
     * again: database corruption is the only one today. Everything else goes
     * through [i]/[w]/[e], which respect the capture window — if this became the
     * ordinary path the log would grow forever with nobody having asked for it.
     *
     * The rotation in [writeRaw] still applies, so it cannot fill the disk.
     */
    fun recordAlways(channel: LogChannel, line: String) {
        Log.w(TAG, line)
        writeRaw(channel, line)
    }

    // ----------------------------------------------------------------
    // File access
    // ----------------------------------------------------------------

    fun readLog(channel: LogChannel): String {
        val f = logFile(channel)
        return if (f.exists()) f.readText() else ""
    }

    fun getLogFile(channel: LogChannel): File = logFile(channel)

    fun clearLog(channel: LogChannel) {
        logFile(channel).takeIf { it.exists() }?.delete()
    }

    // ----------------------------------------------------------------
    // Internal
    // ----------------------------------------------------------------

    private fun writeRaw(channel: LogChannel, line: String) {
        val file = logFile(channel)
        val entry = "${fmt.format(Date())}  $line\n"
        try {
            if (file.exists() && file.length() > LOG_MAX_BYTES) {
                val backup = File(context.filesDir, "${channel.logFileName}.bak")
                file.copyTo(backup, overwrite = true)
                file.delete()
            }
            file.appendText(entry)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to write log entry for $channel", e)
        }
    }
}
