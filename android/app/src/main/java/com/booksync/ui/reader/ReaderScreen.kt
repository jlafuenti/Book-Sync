package com.booksync.ui.reader

import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import android.content.Context
import androidx.work.*
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject

/**
 * ViewModel that loads book pair data and checks if the ebook is downloaded.
 */
@HiltViewModel
class ReaderViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    savedStateHandle: SavedStateHandle,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {
    private val pairId: Int = savedStateHandle["pairId"] ?: 0
    private val workManager = WorkManager.getInstance(context)

    private val _pair = MutableStateFlow<BookPairEntity?>(null)
    val pair = _pair.asStateFlow()

    private val _isReady = MutableStateFlow(false)
    val isReady = _isReady.asStateFlow()

    private val _downloadingProgress = MutableStateFlow<String?>(null)
    val downloadingProgress = _downloadingProgress.asStateFlow()

    init {
        viewModelScope.launch {
            repository.getPairsFlow().collect { pairs ->
                val p = pairs.find { it.id == pairId }
                _pair.value = p
                _isReady.value = p?.ebookDownloaded == true
            }
        }
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                var currentProgress: String? = null
                for (info in workInfos) {
                    if (info.state == WorkInfo.State.RUNNING) {
                        val currentType = info.progress.getString("CURRENT")
                        val id = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                        if (id == pairId) {
                            val progress = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                            val typeLabel = when (currentType) {
                                "EBOOK" -> "Ebook"
                                "AUDIOBOOK" -> "Audiobook"
                                "SYNC_MAP" -> "Sync Data"
                                else -> "files"
                            }
                            currentProgress = if (progress >= 0) {
                                "Downloading $typeLabel ($progress%)..."
                            } else {
                                "Downloading $typeLabel..."
                            }
                        }
                    }
                }
                _downloadingProgress.value = currentProgress
            }
        }
    }

    fun downloadEbook() {
        val p = _pair.value ?: return
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to p.id,
                DownloadWorker.KEY_TYPE to "EBOOK"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork("download_ebook_${p.id}", ExistingWorkPolicy.REPLACE, request)
    }
}

/**
 * ReaderScreen acts as a launcher for the ReaderActivity.
 * When the ebook is downloaded, it immediately launches the Readium-based reader activity.
 * If not downloaded, it shows a download prompt.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReaderScreen(
    pairId: Int,
    onBack: () -> Unit,
    onSwitchToAudio: () -> Unit,
    viewModel: ReaderViewModel = hiltViewModel(),
) {
    val pair by viewModel.pair.collectAsState()
    val isReady by viewModel.isReady.collectAsState()
    val downloadingProgress by viewModel.downloadingProgress.collectAsState()
    val context = LocalContext.current

    // Track whether we've launched the activity
    var hasLaunched by remember { mutableStateOf(false) }

    // Activity result launcher — detects switch-to-audio vs normal back
    val launcher = rememberLauncherForActivityResult(
        contract = androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == ReaderActivity.RESULT_SWITCH_TO_AUDIO) {
            onSwitchToAudio()
        } else {
            onBack()
        }
    }

    // Launch ReaderActivity when ebook is ready
    LaunchedEffect(isReady) {
        if (isReady && !hasLaunched) {
            hasLaunched = true
            val intent = Intent(context, ReaderActivity::class.java).apply {
                putExtra(ReaderActivity.EXTRA_PAIR_ID, pairId)
            }
            launcher.launch(intent)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = pair?.ebookTitle ?: "Reader",
                        maxLines = 1,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                },
                actions = {
                    FilledTonalIconButton(onClick = onSwitchToAudio) {
                        Icon(Icons.Default.Headphones, "Switch to Audio")
                    }
                },
            )
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentAlignment = Alignment.Center,
        ) {
            if (isReady) {
                // Already launched activity — show a brief loading state
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(16.dp))
                    Text("Opening reader...")
                }
            } else {
                // Ebook not downloaded
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.padding(32.dp),
                ) {
                    Icon(
                        Icons.Default.MenuBook,
                        contentDescription = null,
                        modifier = Modifier.size(64.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Spacer(Modifier.height(16.dp))
                    Text(
                        "Ebook Not Downloaded",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Download the ebook to start reading.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(24.dp))
                    if (downloadingProgress != null) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                            Text(downloadingProgress!!, style = MaterialTheme.typography.bodyMedium)
                        }
                    } else {
                        FilledTonalButton(onClick = { viewModel.downloadEbook() }) {
                            Icon(Icons.Default.Download, null)
                            Spacer(Modifier.width(8.dp))
                            Text("Download Ebook")
                        }
                    }
                }
            }
        }
    }
}
