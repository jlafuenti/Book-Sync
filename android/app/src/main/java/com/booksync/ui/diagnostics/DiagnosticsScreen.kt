package com.booksync.ui.diagnostics

import android.app.Activity
import android.content.Intent
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.diagnostics.LogChannel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DiagnosticsScreen(
    onBack: () -> Unit,
    viewModel: DiagnosticsViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val activity = context as? Activity

    LaunchedEffect(Unit) { viewModel.loadLog() }

    val logScrollState = rememberScrollState()
    LaunchedEffect(uiState.logContent) {
        if (uiState.logContent.isNotEmpty()) logScrollState.animateScrollTo(logScrollState.maxValue)
    }

    var selectedMode by remember { mutableStateOf(DiagMode.FIFTEEN_MINUTES) }
    var modeMenuExpanded by remember { mutableStateOf(false) }

    val modeLabel = { mode: DiagMode ->
        when (mode) {
            DiagMode.FIVE_MINUTES    -> "5 minutes"
            DiagMode.FIFTEEN_MINUTES -> "15 minutes"
            DiagMode.ONE_HOUR        -> "1 hour"
            DiagMode.UNTIL_APP_CLOSED -> "Until app is closed"
            DiagMode.UNTIL_STOPPED   -> "Until stopped manually"
        }
    }

    val screenTitle = "${viewModel.channel.label} Diagnostics"

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(screenTitle, fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = {
                        viewModel.shareLog { intent -> activity?.startActivity(intent) }
                    }) {
                        Icon(Icons.Default.Share, contentDescription = "Share log")
                    }
                    IconButton(onClick = { viewModel.clearLog() }) {
                        Icon(Icons.Default.Delete, contentDescription = "Clear log")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {

            // ── Status card ──────────────────────────────────────────
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = if (uiState.isActive)
                        MaterialTheme.colorScheme.primaryContainer
                    else
                        MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(
                        text = uiState.expiryDescription,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.weight(1f)
                    )
                    if (uiState.isActive) {
                        Spacer(Modifier.width(8.dp))
                        OutlinedButton(onClick = { viewModel.disableDiagnostics() }) {
                            Text("Stop")
                        }
                    }
                }
            }

            // ── Start section (shown when inactive) ──────────────────
            if (!uiState.isActive) {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text(
                            "Start Diagnostics",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold
                        )
                        Box {
                            OutlinedButton(
                                onClick = { modeMenuExpanded = true },
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Text(modeLabel(selectedMode))
                            }
                            DropdownMenu(
                                expanded = modeMenuExpanded,
                                onDismissRequest = { modeMenuExpanded = false }
                            ) {
                                DiagMode.entries.forEach { mode ->
                                    DropdownMenuItem(
                                        text = { Text(modeLabel(mode)) },
                                        onClick = {
                                            selectedMode = mode
                                            modeMenuExpanded = false
                                        }
                                    )
                                }
                            }
                        }
                        Button(
                            onClick = { viewModel.enableDiagnostics(selectedMode) },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("Start Diagnostics")
                        }
                    }
                }
            }

            // ── Tip card ─────────────────────────────────────────────
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer
                )
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    if (viewModel.channel == LogChannel.AUTO) {
                        AutoTipContent()
                    } else {
                        AppTipContent()
                    }
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "To export logs: tap the Share icon above → choose Email, Drive, or Messages.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                }
            }

            // ── Log viewer ───────────────────────────────────────────
            if (uiState.logContent.isNotEmpty()) {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("Log", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(8.dp))
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .heightIn(min = 80.dp, max = 320.dp)
                                .verticalScroll(logScrollState)
                                .horizontalScroll(rememberScrollState())
                        ) {
                            Text(
                                text = uiState.logContent,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                lineHeight = 15.sp
                            )
                        }
                    }
                }
            } else {
                Text(
                    "No log entries yet. Start diagnostics and use the app.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun AutoTipContent() {
    Text(
        "If BookSync doesn't appear in your car:",
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold
    )
    val steps = listOf(
        "Open the Android Auto app on your phone.",
        "Tap the Android Auto title 10 times quickly — you should see a \"Developer settings unlocked\" toast.",
        "Go to Settings → Allow apps from unknown sources → toggle ON.",
        "Unplug and re-plug the USB cable to the car to restart Android Auto.",
        "BookSync should now appear in the car launcher."
    )
    steps.forEachIndexed { i, step ->
        Text("${i + 1}. $step", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun AppTipContent() {
    Text(
        "What gets logged:",
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold
    )
    val items = listOf(
        "Library refresh (pairs, ebooks, audiobooks)",
        "File downloads and sync map fetches",
        "Bookmark and progress sync operations",
        "API errors and offline fallbacks",
        "Background sync worker activity"
    )
    items.forEach { item ->
        Text("• $item", style = MaterialTheme.typography.bodySmall)
    }
}
