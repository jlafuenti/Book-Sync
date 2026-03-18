package com.booksync.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.ui.theme.TandemTheme

// Accent colours for each theme swatch
private val themeSwatchColors = mapOf(
    TandemTheme.BLUEPRINT    to Color(0xFF7C3AED),
    TandemTheme.FOREST_NIGHT to Color(0xFF22C55E),
    TandemTheme.EMBER        to Color(0xFFF59E0B),
    TandemTheme.AURORA       to Color(0xFF06B6D4),
    TandemTheme.SLATE        to Color(0xFF38BDF8),
)

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
    val appTheme by viewModel.appTheme.collectAsState()

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
            // ── Colour Theme ─────────────────────────────────────
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    text = "Colour Theme",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    TandemTheme.values().forEach { theme ->
                        ThemeSwatch(
                            color = themeSwatchColors[theme] ?: Color.Gray,
                            label = theme.label,
                            selected = theme == appTheme,
                            onClick = { viewModel.setTheme(theme) }
                        )
                    }
                }
                Text(
                    text = "Current: ${appTheme.label}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            HorizontalDivider()

            // ── Storage Management ────────────────────────────────
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

            // ── Diagnostics ───────────────────────────────────────
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
                    Text("Tandem App Diagnostics")
                }
            }
        }
    }
}

@Composable
fun ThemeSwatch(
    color: Color,
    label: String,
    selected: Boolean,
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .size(36.dp)
            .clip(CircleShape)
            .background(color)
            .then(
                if (selected) Modifier.border(3.dp, MaterialTheme.colorScheme.onSurface, CircleShape)
                else Modifier.border(2.dp, Color.Transparent, CircleShape)
            )
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        if (selected) {
            Text(
                text = "✓",
                color = if (color.luminance() > 0.4f) Color.Black else Color.White,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold
            )
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
