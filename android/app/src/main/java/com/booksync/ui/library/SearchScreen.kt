package com.booksync.ui.library

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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
)

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
            // handle error if needed
        } finally {
            _isLoading.value = false
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(
    onBack: () -> Unit,
    onBookSelect: (Int) -> Unit, // Currently only pairs have a full screen
    onAudioSelect: (Int) -> Unit,
    viewModel: SearchViewModel = hiltViewModel()
) {
    val query by viewModel.query.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val results by viewModel.searchResults.collectAsState()

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
                            }
                        }
                    }
                }
            }
        }
    }
}
