package com.booksync.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.LibraryBooks
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.content.Context
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.PairOpenTarget
import com.booksync.ui.components.EmptyState
import com.booksync.ui.theme.Tandem
import com.booksync.worker.DownloadWorker
import com.booksync.worker.downloadUnavailableMessage
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import javax.inject.Inject

// ============================================================================
// Search result model
// ============================================================================

data class SearchResultItem(
    val id: String,
    val title: String,
    val author: String?,
    val series: String?,
    val seriesIndex: Float?,
    val isEbook: Boolean,
    val isAudiobook: Boolean,
    val pairId: Int? = null,
) {
    /** Extract numeric media ID from the composite id (e.g. "ebook_42" -> 42). */
    val numericId: Int? get() = id.substringAfter("_").toIntOrNull()

    /** Convenience flag for UI partitioning. */
    val isPair: Boolean get() = pairId != null
}

// ============================================================================
// ViewModel
// ============================================================================

@HiltViewModel
class SearchViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    tokenManager: com.booksync.data.remote.TokenManager,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {

    /**
     * Pairing is editor-gated on the server (issue #170). Without this the
     * pairing sheet opened for anyone, and creating the pair answered 403.
     */
    val canEdit: kotlinx.coroutines.flow.StateFlow<Boolean> =
        tokenManager.getRole()
            .map { com.booksync.data.auth.hasMinRole(it, "editor") }
            .stateIn(viewModelScope, kotlinx.coroutines.flow.SharingStarted.WhileSubscribed(5_000), false)

    private val workManager = WorkManager.getInstance(context)

    private val _query = MutableStateFlow("")
    val query = _query.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading = _isLoading.asStateFlow()

    private val _searchResults = MutableStateFlow<List<SearchResultItem>>(emptyList())
    val searchResults = _searchResults.asStateFlow()

    /** True when the last query fell back to the local DB (server unreachable). */
    private val _usingOfflineFallback = MutableStateFlow(false)
    val usingOfflineFallback = _usingOfflineFallback.asStateFlow()

    // --- Pairing state ------------------------------------------------------

    private val _unpairedAudiobooks = MutableStateFlow<List<AudioBookEntity>>(emptyList())
    val unpairedAudiobooks = _unpairedAudiobooks.asStateFlow()

    private val _unpairedEbooks = MutableStateFlow<List<EBookEntity>>(emptyList())
    val unpairedEbooks = _unpairedEbooks.asStateFlow()

    private val _pairingError = MutableStateFlow<String?>(null)
    val pairingError = _pairingError.asStateFlow()

    private var searchJob: Job? = null

    fun updateQuery(newQuery: String) {
        _query.value = newQuery
        searchJob?.cancel()
        if (newQuery.isBlank()) {
            _searchResults.value = emptyList()
            _usingOfflineFallback.value = false
            return
        }
        searchJob = viewModelScope.launch {
            delay(500) // debounce
            performSearch(newQuery)
        }
    }

    fun forceSearch() {
        searchJob?.cancel()
        val q = _query.value
        if (q.isBlank()) return
        viewModelScope.launch { performSearch(q) }
    }

    private suspend fun performSearch(q: String) {
        _isLoading.value = true
        try {
            val response = repository.searchLibrary(q)
            val items = mutableListOf<SearchResultItem>()

            val pairedEbookIds = response.book_pairs.map { it.ebook.id }.toSet()
            val pairedAudiobookIds = response.book_pairs.map { it.audiobook.id }.toSet()

            response.book_pairs.forEach { pair ->
                items += SearchResultItem(
                    id = "pair_${pair.id}",
                    title = pair.ebook.title,
                    author = pair.ebook.author ?: pair.audiobook.author,
                    series = pair.ebook.series ?: pair.audiobook.series,
                    seriesIndex = pair.ebook.series_index ?: pair.audiobook.series_index,
                    isEbook = true,
                    isAudiobook = true,
                    pairId = pair.id,
                )
            }
            response.ebooks.forEach { ebook ->
                if (ebook.id !in pairedEbookIds) {
                    items += SearchResultItem(
                        id = "ebook_${ebook.id}",
                        title = ebook.title,
                        author = ebook.author,
                        series = ebook.series,
                        seriesIndex = ebook.series_index,
                        isEbook = true,
                        isAudiobook = false,
                    )
                }
            }
            response.audiobooks.forEach { audio ->
                if (audio.id !in pairedAudiobookIds) {
                    items += SearchResultItem(
                        id = "audio_${audio.id}",
                        title = audio.title,
                        author = audio.author,
                        series = audio.series,
                        seriesIndex = audio.series_index,
                        isEbook = false,
                        isAudiobook = true,
                    )
                }
            }
            _searchResults.value = sortResults(items)
            _usingOfflineFallback.value = false
        } catch (_: Exception) {
            // Network down / server error — fall back to local DB.
            _searchResults.value = sortResults(searchLocalCache(q))
            _usingOfflineFallback.value = true
        } finally {
            _isLoading.value = false
        }
    }

    /** Client-side filter over the Room-cached pairs/ebooks/audiobooks. */
    private suspend fun searchLocalCache(q: String): List<SearchResultItem> {
        val needle = q.lowercase()
        val pairs = repository.getPairsFlow().first()
        val ebooks = repository.getEbooksFlow().first()
        val audios = repository.getAudiobooksFlow().first()

        val pairedEbookIds = pairs.map { it.ebookId }.toSet()
        val pairedAudioIds = pairs.map { it.audiobookId }.toSet()

        val items = mutableListOf<SearchResultItem>()
        pairs.forEach { pair ->
            val haystack = listOfNotNull(
                pair.ebookTitle,
                pair.audiobookTitle,
                pair.ebookAuthor,
                pair.audiobookAuthor,
            ).joinToString(" ").lowercase()
            if (needle in haystack) {
                items += SearchResultItem(
                    id = "pair_${pair.id}",
                    title = pair.ebookTitle,
                    author = pair.ebookAuthor ?: pair.audiobookAuthor,
                    series = null,
                    seriesIndex = null,
                    isEbook = true,
                    isAudiobook = true,
                    pairId = pair.id,
                )
            }
        }
        ebooks.forEach { ebook ->
            if (ebook.id in pairedEbookIds) return@forEach
            val haystack = listOfNotNull(ebook.title, ebook.author, ebook.series)
                .joinToString(" ").lowercase()
            if (needle in haystack) {
                items += SearchResultItem(
                    id = "ebook_${ebook.id}",
                    title = ebook.title,
                    author = ebook.author,
                    series = ebook.series,
                    seriesIndex = ebook.seriesIndex,
                    isEbook = true,
                    isAudiobook = false,
                )
            }
        }
        audios.forEach { audio ->
            if (audio.id in pairedAudioIds) return@forEach
            val haystack = listOfNotNull(audio.title, audio.author, audio.series)
                .joinToString(" ").lowercase()
            if (needle in haystack) {
                items += SearchResultItem(
                    id = "audio_${audio.id}",
                    title = audio.title,
                    author = audio.author,
                    series = audio.series,
                    seriesIndex = audio.seriesIndex,
                    isEbook = false,
                    isAudiobook = true,
                )
            }
        }
        return items
    }

    /** Series-aware sort — strips "The/A/An" articles, then series, then title. */
    private fun sortResults(items: List<SearchResultItem>): List<SearchResultItem> {
        val articleRegex = "^(the|a|an)\\s+".toRegex(RegexOption.IGNORE_CASE)
        return items.sortedWith(
            compareBy<SearchResultItem, String?>(nullsLast()) {
                it.series?.replace(articleRegex, "")?.lowercase()
            }
                .thenBy(nullsLast()) { it.seriesIndex }
                .thenBy { it.title.replace(articleRegex, "").lowercase() },
        )
    }

    // --- Pairing ------------------------------------------------------------

    fun loadUnpairedAudiobooks() {
        viewModelScope.launch {
            runCatching { _unpairedAudiobooks.value = repository.getUnpairedAudiobooks() }
        }
    }

    fun loadUnpairedEbooks() {
        viewModelScope.launch {
            runCatching { _unpairedEbooks.value = repository.getUnpairedEbooks() }
        }
    }

    fun pairEbookWithAudiobook(ebookId: Int, audiobookId: Int) {
        viewModelScope.launch {
            try {
                repository.createPair(ebookId, audiobookId)
                _pairingError.value = null
                performSearch(_query.value)
            } catch (e: Exception) {
                _pairingError.value = e.message
            }
        }
    }

    fun clearPairingError() { _pairingError.value = null }

    // --- Downloads from search results --------------------------------------
    //
    // Mirrors LibraryViewModel.enqueue so the Downloaded tab's progress / state
    // observers pick these up identically. `idForWorker` is the numeric ID from
    // the SearchResultItem and is used both as the worker's input key and the
    // unique work name. TYPE is "ALL" for a pair, else the appropriate
    // standalone type.

    /** Last download failure worth showing, or null. Rendered as a snackbar. */
    private val _downloadError = MutableStateFlow<String?>(null)
    val downloadError = _downloadError.asStateFlow()

    fun clearDownloadError() { _downloadError.value = null }

    init {
        // Search is reached without ever opening Library, and Library's toast
        // is where download failures used to surface. Without this the whole
        // screen was mute (issue #338).
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { infos ->
                infos.firstOrNull { it.state == WorkInfo.State.FAILED }
                    ?.outputData
                    ?.getString(DownloadWorker.ERROR_KEY)
                    ?.let { _downloadError.value = it }
            }
        }
    }

    /**
     * Which format this pair should reopen in (issue #220), the same lookup Home
     * and Library already make. Search is the one surface that used to skip it.
     */
    suspend fun resolvePairOpenTarget(pairId: Int): PairOpenTarget =
        repository.resolvePairOpenTarget(pairId)

    /**
     * Resolve the row, *then* enqueue the download (issue #338).
     *
     * A search result comes from the server, so on a fresh install it can name
     * an id Room has never seen. This used to enqueue regardless; the worker
     * read Room, found nothing, and failed in ~40 ms without issuing a single
     * request or showing anything. Resolving first both fills the cache — so
     * the row the worker reads is there — and gives us somewhere to put the
     * error when it cannot be filled.
     *
     * The worker resolves too, as the backstop for every other download entry
     * point. Doing it here as well is what makes the failure *immediate and
     * visible* rather than five silent retries away.
     */
    fun downloadFromResult(item: SearchResultItem) {
        val id = item.numericId ?: return
        val type = when {
            item.isPair      -> "ALL"
            item.isEbook     -> "STANDALONE_EBOOK"
            item.isAudiobook -> "STANDALONE_AUDIOBOOK"
            else             -> return
        }
        viewModelScope.launch {
            val found = try {
                when (type) {
                    "ALL"              -> repository.resolvePairById(item.pairId ?: id) != null
                    "STANDALONE_EBOOK" -> repository.resolveEbookById(id) != null
                    else               -> repository.resolveAudiobookById(id) != null
                }
            } catch (e: Exception) {
                _downloadError.value = downloadUnavailableMessage(item.title, e)
                return@launch
            }
            if (!found) {
                _downloadError.value = downloadUnavailableMessage(item.title, cause = null)
                return@launch
            }
            val uniqueName = "download_search_${type.lowercase()}_$id"
            val request = DownloadWorker.request((item.pairId ?: id), type)
            workManager.enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, request)
        }
    }
}

// ============================================================================
// Screen
// ============================================================================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(
    onBack: () -> Unit,
    // One callback taking the whole row rather than an id per section: an id on
    // its own cannot say whether it names a pair, an ebook or an audiobook,
    // which is how standalone ids ended up in pair routes (issue #119). The
    // caller decides where each kind opens — see Routes.searchDestination.
    //
    // The second argument is the pair's open target, resolved here because this
    // is where the repository is (issue #220). Null for standalone rows, and for
    // a pair whose lookup failed.
    onResultSelect: (SearchResultItem, PairOpenTarget?) -> Unit,
    viewModel: SearchViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val canEdit by viewModel.canEdit.collectAsState()
    val scope = rememberCoroutineScope()

    // Resolve which format the user last consumed before routing (issue #220).
    // A failed lookup routes with null rather than swallowing the tap: landing
    // in the reader is what search did before this change, so the worst case is
    // the old behaviour, not a dead button.
    val selectResult: (SearchResultItem) -> Unit = { item ->
        scope.launch {
            val target = item.pairId?.let {
                runCatching { viewModel.resolvePairOpenTarget(it) }.getOrNull()
            }
            onResultSelect(item, target)
        }
    }

    val query by viewModel.query.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val results by viewModel.searchResults.collectAsState()
    val usingOfflineFallback by viewModel.usingOfflineFallback.collectAsState()

    var pairingItem by remember { mutableStateOf<SearchResultItem?>(null) }
    var pairSearchQuery by remember { mutableStateOf("") }

    val unpairedAudiobooks by viewModel.unpairedAudiobooks.collectAsState()
    val unpairedEbooks by viewModel.unpairedEbooks.collectAsState()
    val pairingError by viewModel.pairingError.collectAsState()

    // A download that cannot start says so here (issue #338). Library toasts
    // its own download errors; Search showed nothing at all, so the only trace
    // of a failed tap was a logcat line.
    val downloadError by viewModel.downloadError.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(downloadError) {
        downloadError?.let {
            snackbar.showSnackbar(it, duration = SnackbarDuration.Long)
            viewModel.clearDownloadError()
        }
    }

    // ---- Pairing bottom sheet (unchanged flow, restyled chrome) ----
    if (pairingItem != null) {
        val item = pairingItem!!
        val isEbookBeingPaired = item.isEbook
        LaunchedEffect(item.id) {
            if (isEbookBeingPaired) viewModel.loadUnpairedAudiobooks()
            else viewModel.loadUnpairedEbooks()
        }
        PairingSheet(
            item = item,
            isEbookBeingPaired = isEbookBeingPaired,
            unpairedAudiobooks = unpairedAudiobooks,
            unpairedEbooks = unpairedEbooks,
            pairingError = pairingError,
            pairSearchQuery = pairSearchQuery,
            onPairSearchQueryChange = { pairSearchQuery = it },
            onPair = { counterpartId ->
                val selfId = item.numericId ?: return@PairingSheet
                if (isEbookBeingPaired) viewModel.pairEbookWithAudiobook(selfId, counterpartId)
                else viewModel.pairEbookWithAudiobook(counterpartId, selfId)
                pairingItem = null
                pairSearchQuery = ""
            },
            onDismiss = {
                pairingItem = null
                pairSearchQuery = ""
                viewModel.clearPairingError()
            },
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { viewModel.updateQuery(it) },
                        placeholder = {
                            Text(
                                "Search title, author, series…",
                                color = colors.textMuted,
                                fontSize = 14.sp,
                            )
                        },
                        singleLine = true,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(end = 8.dp),
                        leadingIcon = {
                            Icon(
                                Icons.Default.Search,
                                contentDescription = null,
                                tint = colors.textSecondary,
                                modifier = Modifier.size(18.dp),
                            )
                        },
                        shape = Tandem.shapes.input,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { viewModel.forceSearch() }),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = colors.accent,
                            unfocusedBorderColor = colors.border,
                            focusedContainerColor = colors.bgInput,
                            unfocusedContainerColor = colors.bgInput,
                            focusedTextColor = colors.textPrimary,
                            unfocusedTextColor = colors.textPrimary,
                            cursorColor = colors.accent,
                        ),
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                            tint = colors.textPrimary,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = colors.bgSecondary,
                    titleContentColor = colors.textPrimary,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = colors.bgPrimary,
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {

            // Offline / local-cache banner
            if (usingOfflineFallback) {
                OfflineSearchChip()
            }

            when {
                isLoading -> {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) {
                        CircularProgressIndicator(color = colors.accent)
                    }
                }
                query.isBlank() -> {
                    EmptyState(
                        modifier = Modifier.fillMaxSize(),
                        icon = Icons.Default.Search,
                        title = "Search your library",
                        subtitle = "Find books by title, author, or series across pairs, ebooks, and audiobooks.",
                    )
                }
                results.isEmpty() -> {
                    EmptyState(
                        modifier = Modifier.fillMaxSize(),
                        icon = Icons.AutoMirrored.Filled.LibraryBooks,
                        title = "No matches for \"$query\"",
                        subtitle = if (usingOfflineFallback)
                            "Searching local cache only. Connect to the server for full results."
                        else "Try a different query or check your spelling.",
                    )
                }
                else -> SearchResultsList(
                    results = results,
                    onResultSelect = selectResult,
                    // Hidden for a user who cannot pair (issue #170) — the
                    // sheet's own Pair button hits an editor-gated endpoint.
                    onRequestPair = if (canEdit) ({ pairingItem = it }) else null,
                    onDownload = { viewModel.downloadFromResult(it) },
                )
            }
        }
    }
}

// ============================================================================
// Subcomponents
// ============================================================================

@Composable
private fun OfflineSearchChip() {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(colors.statusWarning.copy(alpha = 0.12f))
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.CloudOff,
            contentDescription = null,
            tint = colors.statusWarning,
            modifier = Modifier.size(16.dp),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            "Offline — searching local cache",
            color = colors.statusWarning,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun SearchResultsList(
    results: List<SearchResultItem>,
    onResultSelect: (SearchResultItem) -> Unit,
    /** Null when this user may not pair (issue #170) — the affordance is hidden. */
    onRequestPair: ((SearchResultItem) -> Unit)?,
    onDownload: (SearchResultItem) -> Unit,
) {
    val pairs     = results.filter { it.isPair }
    val ebooks    = results.filter { !it.isPair && it.isEbook }
    val audiobooks = results.filter { !it.isPair && it.isAudiobook }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (pairs.isNotEmpty()) {
            item { SectionHeader("Pairs", pairs.size) }
            items(pairs, key = { it.id }) { item ->
                ResultRow(
                    item = item,
                    onClick = { onResultSelect(item) },
                    onRequestPair = null, // pairs aren't pairable
                    onDownload = onDownload,
                )
            }
        }
        if (ebooks.isNotEmpty()) {
            item { SectionHeader("Ebooks", ebooks.size) }
            items(ebooks, key = { it.id }) { item ->
                ResultRow(
                    item = item,
                    onClick = { onResultSelect(item) },
                    onRequestPair = onRequestPair,
                    onDownload = onDownload,
                )
            }
        }
        if (audiobooks.isNotEmpty()) {
            item { SectionHeader("Audiobooks", audiobooks.size) }
            items(audiobooks, key = { it.id }) { item ->
                ResultRow(
                    item = item,
                    onClick = { onResultSelect(item) },
                    onRequestPair = onRequestPair,
                    onDownload = onDownload,
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(label: String, count: Int) {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 8.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label.uppercase(),
            color = colors.textSecondary,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.8.sp,
        )
        Spacer(Modifier.width(8.dp))
        Text(
            count.toString(),
            color = colors.textMuted,
            fontSize = 11.sp,
            fontWeight = FontWeight.Medium,
        )
        Spacer(Modifier.width(12.dp))
        HorizontalDivider(color = colors.border, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun ResultRow(
    item: SearchResultItem,
    onClick: () -> Unit,
    /** Null when this user may not pair (issue #170) — the affordance is hidden. */
    onRequestPair: ((SearchResultItem) -> Unit)?,
    onDownload: (SearchResultItem) -> Unit,
) {
    val colors = Tandem.colors
    var overflowOpen by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tandem.shapes.card)
            .background(colors.bgCard)
            .clickable(onClick = onClick)
            .padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Type icon disc
        TypeIcon(item)

        Spacer(Modifier.width(12.dp))

        // Title + author + series
        Column(modifier = Modifier.weight(1f)) {
            if (!item.series.isNullOrBlank()) {
                Text(
                    text = buildString {
                        append(item.series)
                        if (item.seriesIndex != null && item.seriesIndex > 0f) {
                            val idx = if (item.seriesIndex % 1 == 0f)
                                item.seriesIndex.toInt().toString()
                            else item.seriesIndex.toString()
                            append(" #$idx")
                        }
                    },
                    color = colors.accent,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Text(
                item.title,
                color = colors.textPrimary,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 2,
            )
            item.author?.let {
                Text(
                    it,
                    color = colors.textSecondary,
                    fontSize = 12.sp,
                    maxLines = 1,
                )
            }
            Spacer(Modifier.size(4.dp))
            TypeChipRow(item)
        }

        // Pair button for unpaired results, and only for a user who may pair.
        if (!item.isPair && onRequestPair != null) {
            Spacer(Modifier.width(4.dp))
            IconButton(onClick = { onRequestPair(item) }) {
                Icon(
                    Icons.Default.Link,
                    contentDescription = "Pair with counterpart",
                    tint = colors.accent,
                )
            }
        }

        // Overflow menu — a single "Download …" entry. The entry label is
        // contextual ("pair" / "ebook" / "audiobook") and the VM picks the
        // right worker TYPE based on the item shape.
        Box {
            IconButton(onClick = { overflowOpen = true }) {
                Icon(
                    Icons.Default.MoreVert,
                    contentDescription = "More actions",
                    tint = colors.textSecondary,
                )
            }
            DropdownMenu(expanded = overflowOpen, onDismissRequest = { overflowOpen = false }) {
                val label = when {
                    item.isPair      -> "Download pair"
                    item.isEbook     -> "Download ebook"
                    item.isAudiobook -> "Download audiobook"
                    else             -> "Download"
                }
                DropdownMenuItem(
                    text = { Text(label) },
                    leadingIcon = {
                        Icon(
                            Icons.Default.CloudDownload,
                            contentDescription = null,
                            tint = colors.accent,
                        )
                    },
                    onClick = {
                        overflowOpen = false
                        onDownload(item)
                    },
                )
            }
        }
    }
}

@Composable
private fun TypeIcon(item: SearchResultItem) {
    val colors = Tandem.colors
    val (icon: ImageVector, tint) = when {
        item.isPair                       -> Icons.AutoMirrored.Filled.LibraryBooks to colors.accent
        item.isEbook                      -> Icons.AutoMirrored.Filled.MenuBook to colors.statusInfo
        else                              -> Icons.Default.Headphones to colors.statusSuccess
    }
    Box(
        modifier = Modifier
            .size(44.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(tint.copy(alpha = 0.18f)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(22.dp))
    }
}

@Composable
private fun TypeChipRow(item: SearchResultItem) {
    val colors = Tandem.colors
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (item.isPair) {
            TinyChip("Pair", colors.accent)
        } else {
            if (item.isEbook) TinyChip("Ebook", colors.statusInfo)
            if (item.isAudiobook) TinyChip("Audiobook", colors.statusSuccess)
            TinyChip("Unpaired", colors.statusError)
        }
    }
}

@Composable
private fun TinyChip(label: String, tint: androidx.compose.ui.graphics.Color) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(tint.copy(alpha = 0.18f))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(
            label.uppercase(),
            color = tint,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.4.sp,
        )
    }
}

// ---------------------------------------------------------------------------
// Pairing sheet — restyled chrome, same logic as before.
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PairingSheet(
    item: SearchResultItem,
    isEbookBeingPaired: Boolean,
    unpairedAudiobooks: List<AudioBookEntity>,
    unpairedEbooks: List<EBookEntity>,
    pairingError: String?,
    pairSearchQuery: String,
    onPairSearchQueryChange: (String) -> Unit,
    onPair: (counterpartId: Int) -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = Tandem.colors
    val counterparts: List<Any> = if (isEbookBeingPaired) unpairedAudiobooks else unpairedEbooks
    val filtered = if (pairSearchQuery.isBlank()) counterparts else {
        val needle = pairSearchQuery.lowercase()
        counterparts.filter {
            when (it) {
                is AudioBookEntity -> listOfNotNull(it.title, it.author, it.series)
                    .any { s -> needle in s.lowercase() }
                is EBookEntity     -> listOfNotNull(it.title, it.author, it.series)
                    .any { s -> needle in s.lowercase() }
                else -> false
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = colors.bgSecondary,
        shape = Tandem.shapes.modal,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp),
        ) {
            Text(
                "Pair \"${item.title}\"",
                color = colors.textPrimary,
                fontSize = 18.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.size(4.dp))
            Text(
                if (isEbookBeingPaired)
                    "Select an audiobook to pair with this ebook."
                else "Select an ebook to pair with this audiobook.",
                color = colors.textSecondary,
                fontSize = 13.sp,
            )

            if (pairingError != null) {
                Spacer(Modifier.size(8.dp))
                Text(
                    "Error: $pairingError",
                    color = colors.statusError,
                    fontSize = 13.sp,
                )
            }

            Spacer(Modifier.size(16.dp))

            OutlinedTextField(
                value = pairSearchQuery,
                onValueChange = onPairSearchQueryChange,
                placeholder = {
                    Text(
                        if (isEbookBeingPaired) "Search audiobooks…" else "Search ebooks…",
                        color = colors.textMuted,
                        fontSize = 13.sp,
                    )
                },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                leadingIcon = {
                    Icon(Icons.Default.Search, null, tint = colors.textSecondary, modifier = Modifier.size(18.dp))
                },
                shape = Tandem.shapes.input,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = colors.accent,
                    unfocusedBorderColor = colors.border,
                    focusedContainerColor = colors.bgInput,
                    unfocusedContainerColor = colors.bgInput,
                    focusedTextColor = colors.textPrimary,
                    unfocusedTextColor = colors.textPrimary,
                    cursorColor = colors.accent,
                ),
            )

            Spacer(Modifier.size(12.dp))

            if (counterparts.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 32.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        if (isEbookBeingPaired) "No unpaired audiobooks available."
                        else "No unpaired ebooks available.",
                        color = colors.textSecondary,
                        fontSize = 13.sp,
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier.heightIn(max = 420.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    if (isEbookBeingPaired) {
                        items(filtered.filterIsInstance<AudioBookEntity>(), key = { it.id }) { ab ->
                            CounterpartRow(
                                title = ab.title,
                                author = ab.author,
                                series = ab.series,
                                seriesIndex = ab.seriesIndex,
                                formatTag = ab.format,
                                onClick = { onPair(ab.id) },
                            )
                        }
                    } else {
                        items(filtered.filterIsInstance<EBookEntity>(), key = { it.id }) { eb ->
                            CounterpartRow(
                                title = eb.title,
                                author = eb.author,
                                series = eb.series,
                                seriesIndex = eb.seriesIndex,
                                formatTag = eb.format,
                                onClick = { onPair(eb.id) },
                            )
                        }
                    }
                }
                Spacer(Modifier.size(8.dp))
                TextButton(onClick = onDismiss) {
                    Text("Cancel", color = colors.textSecondary)
                }
            }
        }
    }
}

@Composable
private fun CounterpartRow(
    title: String,
    author: String?,
    series: String?,
    seriesIndex: Float?,
    formatTag: String?,
    onClick: () -> Unit,
) {
    val colors = Tandem.colors
    val subtitle = buildString {
        author?.let { append(it) }
        if (!series.isNullOrBlank()) {
            if (isNotEmpty()) append(" · ")
            append(series)
            if (seriesIndex != null && seriesIndex > 0f) {
                val idx = if (seriesIndex % 1 == 0f) seriesIndex.toInt().toString() else seriesIndex.toString()
                append(" #$idx")
            }
        }
    }
    ListItem(
        headlineContent = {
            Text(title, color = colors.textPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
        },
        supportingContent = if (subtitle.isNotBlank()) {
            { Text(subtitle, color = colors.textSecondary, fontSize = 12.sp) }
        } else null,
        trailingContent = formatTag?.takeIf { it.isNotBlank() }?.let { tag ->
            { TinyChip(tag.uppercase(), colors.textMuted) }
        },
        colors = ListItemDefaults.colors(containerColor = colors.bgCard),
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tandem.shapes.card)
            .clickable(onClick = onClick),
    )
}
