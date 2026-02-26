package com.booksync.ui.library

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SearchResultItem(
    val id: String,
    val title: String,
    val author: String?,
    val series: String?,
    val seriesIndex: Float?,
    val isEbook: Boolean,
    val isAudiobook: Boolean,
    val pairId: Int? = null
) {
    /** Extract numeric media ID from the composite id (e.g. "ebook_42" -> 42). */
    val numericId: Int? get() = id.substringAfter("_").toIntOrNull()
}

@HiltViewModel
class SearchViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    private val _query = MutableStateFlow("")
    val query = _query.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading = _isLoading.asStateFlow()

    private val _searchResults = MutableStateFlow<List<SearchResultItem>>(emptyList())
    val searchResults = _searchResults.asStateFlow()

    // Pairing state
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
        viewModelScope.launch {
            performSearch(q)
        }
    }

    private suspend fun performSearch(q: String) {
        _isLoading.value = true
        try {
            val response = repository.searchLibrary(q)
            val items = mutableListOf<SearchResultItem>()
            
            val pairedEbookIds = response.book_pairs.map { it.ebook.id }.toSet()
            val pairedAudiobookIds = response.book_pairs.map { it.audiobook.id }.toSet()

            response.book_pairs.forEach { pair ->
                items.add(
                    SearchResultItem(
                        id = "pair_${pair.id}",
                        title = pair.ebook.title,
                        author = pair.ebook.author ?: pair.audiobook.author,
                        series = pair.ebook.series ?: pair.audiobook.series,
                        seriesIndex = pair.ebook.series_index ?: pair.audiobook.series_index,
                        isEbook = true,
                        isAudiobook = true,
                        pairId = pair.id
                    )
                )
            }
            
            response.ebooks.forEach { ebook ->
                if (ebook.id !in pairedEbookIds) {
                    items.add(
                        SearchResultItem(
                            id = "ebook_${ebook.id}",
                            title = ebook.title,
                            author = ebook.author,
                            series = ebook.series,
                            seriesIndex = ebook.series_index,
                            isEbook = true,
                            isAudiobook = false
                        )
                    )
                }
            }
            
            response.audiobooks.forEach { audio ->
                if (audio.id !in pairedAudiobookIds) {
                    items.add(
                        SearchResultItem(
                            id = "audio_${audio.id}",
                            title = audio.title,
                            author = audio.author,
                            series = audio.series,
                            seriesIndex = audio.series_index,
                            isEbook = false,
                            isAudiobook = true
                        )
                    )
                }
            }
            
            val prefixRegex = "^(the|a|an)\\s+".toRegex(RegexOption.IGNORE_CASE)
            
            _searchResults.value = items.sortedWith(
                compareBy<SearchResultItem, String?>(nullsLast()) { it.series?.replace(prefixRegex, "")?.lowercase() }
                    .thenBy(nullsLast()) { it.seriesIndex }
                    .thenBy { it.title.replace(prefixRegex, "").lowercase() }
            )
            
        } catch (_: Exception) {
        } finally {
            _isLoading.value = false
        }
    }

    // Pairing functions

    fun loadUnpairedAudiobooks() {
        viewModelScope.launch {
            try { _unpairedAudiobooks.value = repository.getUnpairedAudiobooks() }
            catch (_: Exception) {}
        }
    }

    fun loadUnpairedEbooks() {
        viewModelScope.launch {
            try { _unpairedEbooks.value = repository.getUnpairedEbooks() }
            catch (_: Exception) {}
        }
    }

    fun pairEbookWithAudiobook(ebookId: Int, audiobookId: Int) {
        viewModelScope.launch {
            try {
                repository.createPair(ebookId, audiobookId)
                _pairingError.value = null
                // Re-run search to refresh results
                performSearch(_query.value)
            } catch (e: Exception) {
                _pairingError.value = e.message
            }
        }
    }

    fun clearPairingError() { _pairingError.value = null }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(
    onBack: () -> Unit,
    onBookSelect: (Int) -> Unit,
    onAudioSelect: (Int) -> Unit,
    viewModel: SearchViewModel = hiltViewModel()
) {
    val query by viewModel.query.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val results by viewModel.searchResults.collectAsState()

    // Manage dialog state
    var selectedItem by remember { mutableStateOf<SearchResultItem?>(null) }

    // Pairing bottom sheet state
    var showPairSheet by remember { mutableStateOf(false) }
    var pairSearchQuery by remember { mutableStateOf("") }
    // Which item is being paired (the one from the manage dialog)
    var pairingItem by remember { mutableStateOf<SearchResultItem?>(null) }

    val unpairedAudiobooks by viewModel.unpairedAudiobooks.collectAsState()
    val unpairedEbooks by viewModel.unpairedEbooks.collectAsState()
    val pairingError by viewModel.pairingError.collectAsState()

    // ---- Manage Dialog (identical to EbookCard / AudiobookCard manage dialogs) ----
    if (selectedItem != null) {
        val item = selectedItem!!
        AlertDialog(
            onDismissRequest = { selectedItem = null },
            title = {
                Text(if (item.isEbook) "Manage Ebook" else "Manage Audiobook")
            },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    // Show book info
                    Text(item.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    item.author?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    if (!item.series.isNullOrBlank()) {
                        Text(
                            buildString {
                                append(item.series)
                                if (item.seriesIndex != null && item.seriesIndex > 0f) {
                                    append(" #${if (item.seriesIndex % 1 == 0f) item.seriesIndex.toInt().toString() else item.seriesIndex.toString()}")
                                }
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary
                        )
                    }
                    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))

                    // Pair action
                    TextButton(onClick = {
                        pairingItem = item
                        selectedItem = null
                        if (item.isEbook) viewModel.loadUnpairedAudiobooks()
                        else viewModel.loadUnpairedEbooks()
                        showPairSheet = true
                    }) {
                        Icon(Icons.Default.Link, null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(if (item.isEbook) "🎧 Pair with Audiobook" else "📚 Pair with Ebook")
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { selectedItem = null }) { Text("Close") }
            }
        )
    }

    // ---- Pairing Bottom Sheet ----
    if (showPairSheet && pairingItem != null) {
        val item = pairingItem!!
        val isEbookBeingPaired = item.isEbook

        val counterparts = if (isEbookBeingPaired) unpairedAudiobooks else unpairedEbooks
        val filtered = if (pairSearchQuery.isBlank()) {
            counterparts
        } else {
            val q = pairSearchQuery.lowercase()
            counterparts.filter {
                when (it) {
                    is AudioBookEntity -> it.title.lowercase().contains(q) ||
                        (it.author?.lowercase()?.contains(q) == true) ||
                        (it.series?.lowercase()?.contains(q) == true)
                    is EBookEntity -> it.title.lowercase().contains(q) ||
                        (it.author?.lowercase()?.contains(q) == true) ||
                        (it.series?.lowercase()?.contains(q) == true)
                    else -> false
                }
            }
        }

        ModalBottomSheet(
            onDismissRequest = {
                showPairSheet = false
                pairSearchQuery = ""
                pairingItem = null
                viewModel.clearPairingError()
            }
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
                    .padding(bottom = 32.dp)
            ) {
                Text(
                    "Pair \"${item.title}\"",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    if (isEbookBeingPaired) "Select an audiobook to pair with this ebook."
                    else "Select an ebook to pair with this audiobook.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp, bottom = 12.dp)
                )

                if (pairingError != null) {
                    Text(
                        "Error: $pairingError",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(bottom = 8.dp)
                    )
                }

                OutlinedTextField(
                    value = pairSearchQuery,
                    onValueChange = { pairSearchQuery = it },
                    label = { Text(if (isEbookBeingPaired) "Search audiobooks..." else "Search ebooks...") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    leadingIcon = { Icon(Icons.Default.Search, null) }
                )

                Spacer(Modifier.height(12.dp))

                if (counterparts.isEmpty()) {
                    Text(
                        if (isEbookBeingPaired) "No unpaired audiobooks available."
                        else "No unpaired ebooks available.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 24.dp)
                    )
                } else {
                    LazyColumn(
                        modifier = Modifier.heightIn(max = 400.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        if (isEbookBeingPaired) {
                            items(filtered.filterIsInstance<AudioBookEntity>()) { audiobook ->
                                ListItem(
                                    headlineContent = { Text(audiobook.title, fontWeight = FontWeight.Medium) },
                                    supportingContent = {
                                        val parts = listOfNotNull(
                                            audiobook.author,
                                            audiobook.series?.let { s -> "$s${audiobook.seriesIndex?.let { " #${it.toInt()}" } ?: ""}" }
                                        )
                                        if (parts.isNotEmpty()) Text(parts.joinToString(" · "))
                                    },
                                    trailingContent = {
                                        AssistChip(onClick = {}, label = { Text(audiobook.format.uppercase(), style = MaterialTheme.typography.labelSmall) })
                                    },
                                    modifier = Modifier.clickable {
                                        val ebookId = item.numericId ?: return@clickable
                                        viewModel.pairEbookWithAudiobook(ebookId, audiobook.id)
                                        showPairSheet = false
                                        pairSearchQuery = ""
                                        pairingItem = null
                                    }
                                )
                            }
                        } else {
                            items(filtered.filterIsInstance<EBookEntity>()) { ebook ->
                                ListItem(
                                    headlineContent = { Text(ebook.title, fontWeight = FontWeight.Medium) },
                                    supportingContent = {
                                        val parts = listOfNotNull(
                                            ebook.author,
                                            ebook.series?.let { s -> "$s${ebook.seriesIndex?.let { " #${it.toInt()}" } ?: ""}" }
                                        )
                                        if (parts.isNotEmpty()) Text(parts.joinToString(" · "))
                                    },
                                    trailingContent = {
                                        AssistChip(onClick = {}, label = { Text(ebook.format.uppercase(), style = MaterialTheme.typography.labelSmall) })
                                    },
                                    modifier = Modifier.clickable {
                                        val audiobookId = item.numericId ?: return@clickable
                                        viewModel.pairEbookWithAudiobook(ebook.id, audiobookId)
                                        showPairSheet = false
                                        pairSearchQuery = ""
                                        pairingItem = null
                                    }
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    // ---- Main Search UI ----
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { viewModel.updateQuery(it) },
                        placeholder = { Text("Search title, author etc...") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(end = 16.dp),
                        leadingIcon = { Icon(Icons.Default.Search, contentDescription = "Search") },
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { viewModel.forceSearch() }),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = MaterialTheme.colorScheme.primary,
                            unfocusedBorderColor = MaterialTheme.colorScheme.outline
                        )
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            if (isLoading) {
                item {
                    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(modifier = Modifier.size(32.dp))
                    }
                }
            }

            if (!isLoading && query.isNotBlank() && results.isEmpty()) {
                item {
                    Text("No results found.", modifier = Modifier.padding(16.dp))
                }
            }

            if (results.isNotEmpty()) {
                items(results, key = { it.id }) { item ->
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        onClick = {
                            if (item.pairId != null) {
                                onBookSelect(item.pairId)
                            } else {
                                selectedItem = item
                            }
                        }
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            // Series Info
                            if (!item.series.isNullOrBlank()) {
                                Text(
                                    text = buildString {
                                        append(item.series)
                                        if (item.seriesIndex != null && item.seriesIndex > 0f) {
                                            val indexStr = if (item.seriesIndex % 1 == 0f) item.seriesIndex.toInt().toString() else item.seriesIndex.toString()
                                            append(" #$indexStr")
                                        }
                                    },
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.padding(bottom = 4.dp)
                                )
                            }
                        
                            Text(text = item.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                            item.author?.let { Text(text = it, style = MaterialTheme.typography.bodyMedium) }
                            
                            Spacer(Modifier.height(8.dp))
                            
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                if (item.isEbook) {
                                    AssistChip(onClick = {}, label = { Text("Ebook") }, leadingIcon = { Text("📚") })
                                }
                                if (item.isAudiobook) {
                                    AssistChip(onClick = {}, label = { Text("Audiobook") }, leadingIcon = { Text("🎧") })
                                }
                                if (item.pairId == null) {
                                    AssistChip(
                                        onClick = {},
                                        label = { Text("Unpaired") },
                                        colors = AssistChipDefaults.assistChipColors(
                                            containerColor = MaterialTheme.colorScheme.errorContainer,
                                            labelColor = MaterialTheme.colorScheme.onErrorContainer
                                        )
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
