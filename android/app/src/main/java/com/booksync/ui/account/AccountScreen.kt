package com.booksync.ui.account

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.net.toUri
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.data.remote.normalizeServerUrl
import com.booksync.BuildConfig
import com.booksync.ui.components.ActionRow
import com.booksync.ui.theme.Tandem
import com.booksync.ui.theme.TandemTheme
import kotlinx.coroutines.launch

// ---------------------------------------------------------------------------
// Theme swatch colors — locked to match each TandemTheme's accent (mirrors
// the swatches on the web Account page).
// ---------------------------------------------------------------------------

private val themeSwatchColors = mapOf(
    TandemTheme.BLUEPRINT    to Color(0xFF7C3AED),
    TandemTheme.FOREST_NIGHT to Color(0xFF22C55E),
    TandemTheme.EMBER        to Color(0xFFF59E0B),
    TandemTheme.AURORA       to Color(0xFF06B6D4),
    TandemTheme.SLATE        to Color(0xFF38BDF8),
)

/**
 * Account tab — bottom-nav destination. Replaces the old overlay [SettingsScreen].
 *
 * Sections (top → bottom):
 *   1. User card   — avatar, username, "Change password" button
 *   2. Appearance  — 5 theme swatches (persists to DataStore + server)
 *   3. Storage     — auto-cleanup toggles, "Clear all downloads"
 *   4. Diagnostics — Auto / App log capture (overlay)
 *   5. About       — version + build
 *   6. Logout      — destructive-tinted button
 *
 * @param onDiagnosticsAuto / [onDiagnosticsApp] navigate to `diagnostics/{channel}`
 * @param onClearAllDownloads wired by BookSyncNavigation to the DownloadedViewModel
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AccountScreen(
    onDiagnosticsAuto: () -> Unit,
    onDiagnosticsApp: () -> Unit,
    onClearAllDownloads: () -> Unit = {},
    viewModel: AccountViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val autoCleanupEbooks     by viewModel.autoCleanupEbooks.collectAsState()
    val autoCleanupAudiobooks by viewModel.autoCleanupAudiobooks.collectAsState()
    val appTheme              by viewModel.appTheme.collectAsState()
    val user                  by viewModel.user.collectAsState()
    val isOnline              by viewModel.isOnline.collectAsState()
    val serverUrl             by viewModel.serverUrl.collectAsState()
    val deviceName             by viewModel.deviceName.collectAsState()

    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var showChangePassword     by remember { mutableStateOf(false) }
    var confirmLogout          by remember { mutableStateOf(false) }
    var confirmLogoutAll       by remember { mutableStateOf(false) }
    var confirmClearDownloads  by remember { mutableStateOf(false) }

    if (showChangePassword) {
        ChangePasswordSheet(
            isOnline = isOnline,
            onDismiss = { showChangePassword = false },
            onSuccess = {
                showChangePassword = false
                scope.launch { snackbar.showSnackbar("Password updated") }
            },
        )
    }

    if (confirmLogout) {
        ConfirmDialog(
            title = "Log out?",
            message = "You'll need to sign in again to access your library.",
            confirmLabel = "Log out",
            destructive = true,
            onConfirm = {
                confirmLogout = false
                viewModel.logout()
            },
            onDismiss = { confirmLogout = false },
        )
    }

    // Issue #250. Separate confirm from "Log out?" on purpose: this one cannot
    // be undone from here and takes down every other device too, including one
    // that is mid-book with positions it has not pushed yet
    // (docs/position-sync-contract.md).
    if (confirmLogoutAll) {
        ConfirmDialog(
            title = "Sign out everywhere?",
            message = "This ends every session on your account — this phone, the web app, and any " +
                "other device. Use it if a device has been lost. A signed-out device cannot send " +
                "reading positions it has not synced yet; they wait on that device until it signs " +
                "in again.",
            confirmLabel = "Sign out everywhere",
            destructive = true,
            onConfirm = {
                confirmLogoutAll = false
                viewModel.logoutAll()
            },
            onDismiss = { confirmLogoutAll = false },
        )
    }

    if (confirmClearDownloads) {
        ConfirmDialog(
            title = "Clear all downloads?",
            message = "Every downloaded ebook and audiobook will be removed from this device. Your progress stays safe on the server.",
            confirmLabel = "Clear downloads",
            destructive = true,
            onConfirm = {
                confirmClearDownloads = false
                onClearAllDownloads()
                scope.launch { snackbar.showSnackbar("Downloads cleared") }
            },
            onDismiss = { confirmClearDownloads = false },
        )
    }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        "Account",
                        color = colors.textPrimary,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = colors.bgSecondary,
                    titleContentColor = colors.textPrimary,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = colors.bgPrimary,
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            item {
                UserCard(
                    username = user?.username ?: "Loading…",
                    email = user?.email,
                    isOnline = isOnline,
                    onChangePassword = { showChangePassword = true },
                )
            }

            item { SectionTitle("Appearance") }
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(
                        "Color theme",
                        color = colors.textPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TandemTheme.entries.forEach { theme ->
                            ThemeSwatch(
                                color = themeSwatchColors[theme] ?: Color.Gray,
                                selected = theme == appTheme,
                                onClick = { viewModel.setTheme(theme) },
                            )
                        }
                    }
                    Text(
                        "Current: ${appTheme.label}",
                        color = colors.textMuted,
                        fontSize = 12.sp,
                    )
                }
            }

            item { SectionTitle("Storage") }
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard),
                ) {
                    ToggleRow(
                        title = "Auto-cleanup ebooks",
                        description = "Remove downloaded ebooks after you finish them.",
                        checked = autoCleanupEbooks,
                        onCheckedChange = viewModel::setAutoCleanupEbooks,
                    )
                    Divider()
                    ToggleRow(
                        title = "Auto-cleanup audiobooks",
                        description = "Remove downloaded audiobooks after you finish them.",
                        checked = autoCleanupAudiobooks,
                        onCheckedChange = viewModel::setAutoCleanupAudiobooks,
                    )
                    Divider()
                    ActionRow(
                        title = "Clear all downloads",
                        description = "Delete every downloaded file. Progress stays on the server.",
                        destructive = true,
                        onClick = { confirmClearDownloads = true },
                    )
                }
            }

            item { SectionTitle("Device") }
            item {
                var deviceNameEdit by remember(deviceName) { mutableStateOf(deviceName) }
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    OutlinedTextField(
                        value = deviceNameEdit,
                        onValueChange = { if (it.length <= 60) deviceNameEdit = it },
                        label = { Text("Device name") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    )
                    Button(
                        onClick = { viewModel.setDeviceName(deviceNameEdit.trim()) },
                        enabled = deviceNameEdit.isNotBlank() && deviceNameEdit.trim() != deviceName,
                        modifier = Modifier.fillMaxWidth().height(44.dp),
                    ) {
                        Text("Save")
                    }
                    Text(
                        "Shown to your other devices in Session History (e.g. \"Kitchen Pixel\").",
                        color = colors.textMuted,
                        fontSize = 12.sp,
                    )
                    Text(
                        "Reset to default",
                        color = colors.accent,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable { viewModel.resetDeviceName() },
                    )
                }
            }

            item { SectionTitle("Diagnostics") }
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard),
                ) {
                    ActionRow(
                        title = "Android Auto logs",
                        description = "Capture events from the in-car experience.",
                        onClick = onDiagnosticsAuto,
                    )
                    Divider()
                    ActionRow(
                        title = "Tandem app logs",
                        description = "Capture in-app events for support.",
                        onClick = onDiagnosticsApp,
                    )
                }
            }

            item { SectionTitle("About") }
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    AboutRow("Version", BuildConfig.VERSION_NAME)
                    AboutRow("Build",   BuildConfig.VERSION_CODE.toString())
                }
            }

            item { SectionTitle("Server") }
            item {
                val context = LocalContext.current
                var serverUrlEdit by remember(serverUrl) { mutableStateOf(serverUrl) }
                // Issue #149: a refused URL leaves the app running on the old server,
                // so the failure has to be said out loud — nothing else changes.
                val serverUrlError by viewModel.serverUrlError.collectAsState()
                // The field is only `remember`ed while the error lives in the
                // ViewModel, so scrolling this LazyColumn item out and back — or a
                // rotation — resets the text to the stored value and leaves the red
                // border and message describing input that is no longer there.
                LaunchedEffect(Unit) { viewModel.clearServerUrlError() }
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    OutlinedTextField(
                        value = serverUrlEdit,
                        onValueChange = {
                            serverUrlEdit = it
                            viewModel.clearServerUrlError()
                        },
                        label = { Text("Server URL") },
                        singleLine = true,
                        isError = serverUrlError != null,
                        modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Uri,
                            imeAction = ImeAction.Done,
                        ),
                    )
                    Button(
                        onClick = { viewModel.saveServerUrl(serverUrlEdit.trim()) },
                        // Normalized comparison: retyping the same server without
                        // its scheme must not kill and relaunch the process for a
                        // value that ends up identical. Still enabled when it does
                        // not normalize, so the button can surface the error.
                        enabled = serverUrlEdit.isNotBlank() &&
                            normalizeServerUrl(serverUrlEdit).let { it == null || it != serverUrl },
                        modifier = Modifier.fillMaxWidth().height(44.dp),
                    ) {
                        Text("Save")
                    }
                    Text(
                        serverUrlError ?: "Changing this signs you out; the new server applies immediately.",
                        color = if (serverUrlError != null) colors.statusError else colors.textMuted,
                        fontSize = 12.sp,
                    )
                }
            }

            // Shortcut into the browser-based version of Book Sync, for uploads /
            // admin operations that aren't surfaced in the Android client yet.
            item {
                val context = LocalContext.current
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard),
                ) {
                    ActionRow(
                        title = "Open web app",
                        description = "Launch Tandem in your browser.",
                        trailingIcon = Icons.Default.OpenInBrowser,
                        onClick = {
                            context.startActivity(
                                Intent(Intent.ACTION_VIEW, serverUrl.toUri())
                            )
                        },
                    )
                }
            }

            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.statusError.copy(alpha = 0.12f))
                        .clickable { confirmLogout = true }
                        .padding(vertical = 14.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.AutoMirrored.Filled.Logout,
                            contentDescription = null,
                            tint = colors.statusError,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(10.dp))
                        Text(
                            "Log out",
                            color = colors.statusError,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
            }

            // Sign out everywhere (issue #250). Under Log out, and quieter than
            // it: the per-device sign-out is the one people want, this is the
            // one for a lost device. It is also the only normal user action
            // that still bumps `token_version`.
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard),
                ) {
                    ActionRow(
                        title = "Sign out everywhere",
                        description = "End every session on this account, on all devices.",
                        destructive = true,
                        onClick = { confirmLogoutAll = true },
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

@Composable
private fun SectionTitle(label: String) {
    val colors = Tandem.colors
    Text(
        text = label.uppercase(),
        color = colors.textSecondary,
        fontSize = 11.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.8.sp,
        modifier = Modifier.padding(start = 4.dp, top = 4.dp),
    )
}

@Composable
private fun UserCard(
    username: String,
    email: String?,
    isOnline: Boolean,
    onChangePassword: () -> Unit,
) {
    val colors = Tandem.colors
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tandem.shapes.card)
            .background(colors.bgCard)
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            // Avatar (initials fallback)
            Box(
                modifier = Modifier
                    .size(56.dp)
                    .clip(CircleShape)
                    .background(colors.accent.copy(alpha = 0.20f)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = username.take(1).uppercase(),
                    color = colors.accent,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.width(14.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    username,
                    color = colors.textPrimary,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                if (!email.isNullOrBlank()) {
                    Text(
                        email,
                        color = colors.textSecondary,
                        fontSize = 13.sp,
                    )
                }
            }
        }
        Spacer(Modifier.height(14.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(Tandem.shapes.button)
                .background(colors.accent.copy(alpha = 0.15f))
                .clickable(enabled = isOnline, onClick = onChangePassword)
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Default.Key,
                contentDescription = null,
                tint = if (isOnline) colors.accent else colors.textMuted,
                modifier = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(10.dp))
            Text(
                if (isOnline) "Change password" else "Change password (offline)",
                color = if (isOnline) colors.accent else colors.textMuted,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.weight(1f))
            Icon(
                Icons.Default.ChevronRight,
                contentDescription = null,
                tint = if (isOnline) colors.accent else colors.textMuted,
                modifier = Modifier.size(18.dp),
            )
        }
    }
}

@Composable
private fun ThemeSwatch(
    color: Color,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val borderColor = if (selected) Tandem.colors.textPrimary else Color.Transparent
    Box(
        modifier = Modifier
            .size(38.dp)
            .clip(CircleShape)
            .background(color)
            .border(3.dp, borderColor, CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        if (selected) {
            Text(
                text = "✓",
                color = if (color.luminance() > 0.4f) Color.Black else Color.White,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
private fun ToggleRow(
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, color = colors.textPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(description, color = colors.textSecondary, fontSize = 12.sp)
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Color.White,
                checkedTrackColor = colors.accent,
                uncheckedThumbColor = colors.textMuted,
                uncheckedTrackColor = colors.bgInput,
                uncheckedBorderColor = colors.border,
            ),
        )
    }
}


@Composable
private fun AboutRow(label: String, value: String) {
    val colors = Tandem.colors
    Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = colors.textSecondary, fontSize = 13.sp, modifier = Modifier.weight(1f))
        Text(value, color = colors.textPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun Divider() {
    val colors = Tandem.colors
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(1.dp)
            .background(colors.border),
    )
}

@Composable
private fun ConfirmDialog(
    title: String,
    message: String,
    confirmLabel: String,
    destructive: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = Tandem.colors
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, color = colors.textPrimary, fontWeight = FontWeight.SemiBold) },
        text = { Text(message, color = colors.textSecondary) },
        confirmButton = {
            androidx.compose.material3.TextButton(onClick = onConfirm) {
                Text(
                    confirmLabel,
                    color = if (destructive) colors.statusError else colors.accent,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        },
        dismissButton = {
            androidx.compose.material3.TextButton(onClick = onDismiss) {
                Text("Cancel", color = colors.textSecondary)
            }
        },
        containerColor = colors.bgSecondary,
    )
}
