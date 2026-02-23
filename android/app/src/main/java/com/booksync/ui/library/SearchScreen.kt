package com.booksync.ui.library

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
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
import com.booksync.data.remote.AudioBookResponse
import com.booksync.data.remote.BookPairResponse
import com.booksync.data.remote.EBookResponse
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class SearchViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    private val _query = MutableStateFlow("")
    val query = _query.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading = _isLoading.asStateFlow()

    private val _ebooks = MutableStateFlow<List<EBookResponse>>(emptyList())
    val ebooks = _ebooks.asStateFlow()

    private val _audiobooks = MutableStateFlow<List<AudioBookResponse>>(emptyList())
    val audiobooks = _audiobooks.asStateFlow()

    private val _pairs = MutableStateFlow<List<BookPairResponse>>(emptyList())
    val pairs = _pairs.asStateFlow()

    private var searchJob: Job? = null

    fun updateQuery(newQuery: String) {
        _query.value = newQuery
        searchJob?.cancel()
        if (newQuery.isBlank()) {
            _ebooks.value = emptyList()
            _audiobooks.value = emptyList()
            _pairs.value = emptyList()
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
            _ebooks.value = response.ebooks
            _audiobooks.value = response.audiobooks
            _pairs.value = response.book_pairs
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
    val ebooks by viewModel.ebooks.collectAsState()
    val audiobooks by viewModel.audiobooks.collectAsState()
    val pairs by viewModel.pairs.collectAsState()

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
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back")
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

            if (!isLoading && query.isNotBlank() && ebooks.isEmpty() && audiobooks.isEmpty() && pairs.isEmpty()) {
                item {
                    Text("No results found.", modifier = Modifier.padding(16.dp))
                }
            }

            if (pairs.isNotEmpty()) {
                item {
                    Text("Matched Pairs", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                }
                items(pairs) { pair ->
                    Card(modifier = Modifier.fillMaxWidth(), onClick = { onBookSelect(pair.id) }) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(text = pair.ebook.title, fontWeight = FontWeight.Bold)
                            pair.ebook.author?.let { Text(text = it, style = MaterialTheme.typography.bodyMedium) }
                            Spacer(Modifier.height(8.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                AssistChip(onClick = {}, label = { Text("Pair") }, leadingIcon = { Text("📚🎧") })
                            }
                        }
                    }
                }
            }

            if (ebooks.isNotEmpty()) {
                item {
                    Text("Ebooks", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                }
                items(ebooks) { ebook ->
                    Card(modifier = Modifier.fillMaxWidth()) { // No on-click for standalone ebook yet
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(text = ebook.title, fontWeight = FontWeight.Bold)
                            ebook.author?.let { Text(text = it, style = MaterialTheme.typography.bodyMedium) }
                            Spacer(Modifier.height(8.dp))
                            AssistChip(onClick = {}, label = { Text(ebook.format) }, leadingIcon = { Text("📚") })
                        }
                    }
                }
            }

            if (audiobooks.isNotEmpty()) {
                item {
                    Text("Audiobooks", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                }
                items(audiobooks) { audio ->
                    Card(modifier = Modifier.fillMaxWidth()) { // No on-click for standalone audiobook yet
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(text = audio.title, fontWeight = FontWeight.Bold)
                            audio.author?.let { Text(text = it, style = MaterialTheme.typography.bodyMedium) }
                            Spacer(Modifier.height(8.dp))
                            AssistChip(onClick = {}, label = { Text(audio.format) }, leadingIcon = { Text("🎧") })
                        }
                    }
                }
            }
        }
    }
}
