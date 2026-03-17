package com.booksync.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    onAutoDiagnosticsClick: () -> Unit = {},
    onAppDiagnosticsClick: () -> Unit = {},
    viewModel: SettingsViewModel = hiltViewModel()
) {
    val autoCleanupEbooks by viewModel.autoCleanupEbooks.collectAsState()
    val autoCleanupAudiobooks by viewModel.autoCleanupAudiobooks.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(24.dp)
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text(
                    text = "Storage Management",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold
                )

                AutoCleanupItem(
                    title = "Auto-Cleanup Ebooks",
                    description = "Automatically delete local ebook files after finishing them to save space.",
                    checked = autoCleanupEbooks,
                    onCheckedChange = { viewModel.setAutoCleanupEbooks(it) }
                )

                AutoCleanupItem(
                    title = "Auto-Cleanup Audiobooks",
                    description = "Automatically delete local audiobook files after finishing them to save space.",
                    checked = autoCleanupAudiobooks,
                    onCheckedChange = { viewModel.setAutoCleanupAudiobooks(it) }
                )
            }

            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text(
                    text = "Diagnostics",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold
                )
                OutlinedButton(
                    onClick = onAutoDiagnosticsClick,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Android Auto Diagnostics")
                }
                OutlinedButton(
                    onClick = onAppDiagnosticsClick,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("BookSync App Diagnostics")
                }
            }
        }
    }
}

@Composable
fun AutoCleanupItem(
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Column(
            modifier = Modifier
                .weight(1f)
                .padding(end = 16.dp)
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange
        )
    }
}
