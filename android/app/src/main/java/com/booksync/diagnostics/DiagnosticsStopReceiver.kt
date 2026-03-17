package com.booksync.diagnostics

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.booksync.ui.diagnostics.notificationId
import dagger.hilt.android.EntryPointAccessors

@dagger.hilt.EntryPoint
@dagger.hilt.InstallIn(dagger.hilt.components.SingletonComponent::class)
interface DiagnosticLoggerEntryPoint {
    fun diagnosticLogger(): DiagnosticLogger
}

class DiagnosticsStopReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val channelName = intent?.getStringExtra("channel") ?: LogChannel.AUTO.name
        val channel = LogChannel.entries.firstOrNull { it.name == channelName } ?: LogChannel.AUTO

        val entryPoint = EntryPointAccessors.fromApplication(
            context.applicationContext,
            DiagnosticLoggerEntryPoint::class.java
        )
        entryPoint.diagnosticLogger().disable(channel)

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.cancel(channel.notificationId())
    }
}
