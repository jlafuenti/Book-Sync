package com.booksync.ui.series

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.R
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.compose.ui.platform.LocalContext
import android.widget.Toast
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SeriesScreen(
    onPairSelect: (Int) -> Unit = {},
    viewModel: SeriesViewModel = hiltViewModel(),
) {
    val refreshing by viewModel.refreshing.collectAsState()
    val refreshMessage by viewModel.refreshMessage.collectAsState()
    val searchQuery by viewModel.searchQuery.collectAsState()
    val sortBy by viewModel.sortBy.collectAsState()
    val seriesData by viewModel.seriesData.collectAsState()
    val unpairedAudiobooks by viewModel.unpairedAudiobooks.collectAsState()
    val unpairedEbooks by viewModel.unpairedEbooks.collectAsState()
    val pairingError by viewModel.pairingError.collectAsState()

    val (allGroups, unseriedItems) = seriesData

    val context = LocalContext.current
    LaunchedEffect(refreshMessage) {
        if (refreshMessage != null) {
            Toast.makeText(context, refreshMessage, Toast.LENGTH_SHORT).show()
            viewModel.clearRefreshMessage()
        }
    }

    var showSyncedOnly by remember { mutableStateOf(false) }

    // Manage dialog state
    var selectedItem by remember { mutableStateOf<SeriesItem?>(null) }
    var showPairSheet by remember { mutableStateOf(false) }
    var pairSearchQuery by remember { mutableStateOf("") }
    var showUnlinkConfirm by remember { mutableStateOf(false) }

    // Client-side filter
    val filteredGroups = remember(allGroups, searchQuery, sortBy, showSyncedOnly) {
        val q = searchQuery.lowercase()
        var filtered = if (q.isBlank()) allGroups else {
            allGroups.filter { g ->
                g.name.lowercase().contains(q) ||
                g.author?.lowercase()?.contains(q) == true ||
                g.items.any { it.title.lowercase().contains(q) }
            }
        }
        if (showSyncedOnly) {
            filtered = filtered.mapNotNull { g ->
                val syncedItems = g.items.filter { it.isPaired && it.status == "synced" }
                if (syncedItems.isEmpty()) null else g.copy(items = syncedItems)
            }
        }
        val prefixRegex = "^(the|a|an)\\s+".toRegex(RegexOption.IGNORE_CASE)
        when (sortBy) {
            SeriesSort.NAME -> filtered.sortedBy { it.name.replace(prefixRegex, "").lowercase() }
            SeriesSort.COUNT -> filtered.sortedByDescending { it.items.size }
        }
    }

    val filteredUnsorted = remember(unseriedItems, searchQuery, showSyncedOnly) {
        val q = searchQuery.lowercase()
        val result = if (q.isBlank()) unseriedItems
        else unseriedItems.filter {
            it.title.lowercase().contains(q) ||
            it.author?.lowercase()?.contains(q) == true
        }
        if (showSyncedOnly) result.filter { it.isPaired && it.status == "synced" } else result
    }

    // Track expanded state
    var expandedSeries by remember { mutableStateOf(setOf<String>()) }

    // ---- Manage Dialog ----
    if (selectedItem != null) {
        val item = selectedItem!!
        AlertDialog(
            onDismissRequest = { selectedItem = null },
            title = {
                Text(
                    when {
                        item.isPaired -> "Manage Book Pair"
                        item.hasEbook -> "Manage Ebook"
                        else -> "Manage Audiobook"
                    }
                )
            },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    // Book info
                    Text(item.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    item.author?.let { Text(it, style = MaterialTheme.typography.bodySmall) }

                    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))

                    if (item.isPaired && item.pairId != null) {
                        // ---- Paired item actions ----
                        // Download ebook
                        if (!item.ebookDownloaded) {
                            TextButton(onClick = {
                                viewModel.downloadPairEbook(item.pairId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Download, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("📚 Download Ebook")
                            }
                        } else {
                            TextButton(onClick = {
                                viewModel.deletePairEbook(item.pairId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Delete, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("📚 Delete Ebook")
                            }
                        }
                        // Download audiobook
                        if (!item.audiobookDownloaded) {
                            TextButton(onClick = {
                                viewModel.downloadPairAudiobook(item.pairId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Download, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("🎧 Download Audiobook")
                            }
                        } else {
                            TextButton(onClick = {
                                viewModel.deletePairAudiobook(item.pairId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Delete, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("🎧 Delete Audiobook")
                            }
                        }
                        HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                        // Unlink pair
                        TextButton(onClick = { showUnlinkConfirm = true }) {
                            Icon(Icons.Default.LinkOff, null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("Unlink Pair")
                        }

                    } else if (item.hasEbook && item.ebookId != null) {
                        // ---- Standalone ebook actions ----
                        if (!item.ebookDownloaded) {
                            TextButton(onClick = {
                                viewModel.downloadStandaloneEbook(item.ebookId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Download, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("📚 Download Ebook")
                            }
                        } else {
                            TextButton(onClick = {
                                viewModel.deleteStandaloneEbook(item.ebookId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Delete, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("📚 Delete Ebook")
                            }
                        }
                        HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                        // Pair with audiobook
                        TextButton(onClick = {
                            selectedItem = null
                            viewModel.loadUnpairedAudiobooks()
                            showPairSheet = true
                        }) {
                            Icon(Icons.Default.Link, null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("🎧 Pair with Audiobook")
                        }

                    } else if (item.hasAudiobook && item.audiobookId != null) {
                        // ---- Standalone audiobook actions ----
                        if (!item.audiobookDownloaded) {
                            TextButton(onClick = {
                                viewModel.downloadStandaloneAudiobook(item.audiobookId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Download, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("🎧 Download Audiobook")
                            }
                        } else {
                            TextButton(onClick = {
                                viewModel.deleteStandaloneAudiobook(item.audiobookId)
                                selectedItem = null
                            }) {
                                Icon(Icons.Default.Delete, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("🎧 Delete Audiobook")
                            }
                        }
                        HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                        // Pair with ebook
                        TextButton(onClick = {
                            selectedItem = null
                            viewModel.loadUnpairedEbooks()
                            showPairSheet = true
                        }) {
                            Icon(Icons.Default.Link, null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("📚 Pair with Ebook")
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { selectedItem = null }) { Text("Close") }
            },
        )
    }

    // ---- Unlink confirmation ----
    if (showUnlinkConfirm && selectedItem != null) {
        AlertDialog(
            onDismissRequest = { showUnlinkConfirm = false },
            title = { Text("Unlink Pair?") },
            text = { Text("This will remove the pairing between the ebook and audiobook. The files themselves will not be deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    selectedItem?.pairId?.let { viewModel.unlinkPair(it) }
                    showUnlinkConfirm = false
                    selectedItem = null
                }) { Text("Unlink", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { showUnlinkConfirm = false }) { Text("Cancel") }
            },
        )
    }

    // ---- Pairing Bottom Sheet ----
    if (showPairSheet && selectedItem != null) {
        val item = selectedItem!!
        val isEbookBeingPaired = item.hasEbook

        val counterparts: List<Any> = if (isEbookBeingPaired) unpairedAudiobooks else unpairedEbooks
        val filtered = if (pairSearchQuery.isBlank()) counterparts else {
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
                selectedItem = null
                viewModel.clearPairingError()
            }
        ) {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                Text(
                    if (isEbookBeingPaired) "Select Audiobook to Pair" else "Select Ebook to Pair",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.height(8.dp))

                OutlinedTextField(
                    value = pairSearchQuery,
                    onValueChange = { pairSearchQuery = it },
                    label = { Text("Search...") },
                    leadingIcon = { Icon(Icons.Default.Search, null) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                Spacer(Modifier.height(8.dp))

                pairingError?.let {
                    Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                    Spacer(Modifier.height(4.dp))
                }

                // List counterparts
                LazyColumn(modifier = Modifier.heightIn(max = 400.dp)) {
                    if (isEbookBeingPaired) {
                        items(filtered.filterIsInstance<AudioBookEntity>(), key = { it.id }) { audio ->
                            ListItem(
                                headlineContent = { Text(audio.title) },
                                supportingContent = { audio.author?.let { Text(it) } },
                                leadingContent = { Text("🎧") },
                                modifier = Modifier.clickable {
                                    item.ebookId?.let { ebookId ->
                                        viewModel.createPair(ebookId, audio.id)
                                        showPairSheet = false
                                        pairSearchQuery = ""
                                        selectedItem = null
                                    }
                                }
                            )
                        }
                    } else {
                        items(filtered.filterIsInstance<EBookEntity>(), key = { it.id }) { ebook ->
                            ListItem(
                                headlineContent = { Text(ebook.title) },
                                supportingContent = { ebook.author?.let { Text(it) } },
                                leadingContent = { Text("📚") },
                                modifier = Modifier.clickable {
                                    item.audiobookId?.let { audiobookId ->
                                        viewModel.createPair(ebook.id, audiobookId)
                                        showPairSheet = false
                                        pairSearchQuery = ""
                                        selectedItem = null
                                    }
                                }
                            )
                        }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Series", fontWeight = FontWeight.Bold, fontSize = 18.sp) },
                navigationIcon = {
                    Icon(
                        painter = painterResource(id = R.drawable.ic_launcher_foreground),
                        contentDescription = "Tandem",
                        tint = Color.Unspecified,
                        modifier = Modifier
                            .padding(start = 8.dp)
                            .size(40.dp)
                            .clip(RoundedCornerShape(8.dp))
                    )
                },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, "Refresh")
                    }
                },
                windowInsets = WindowInsets(0, 0, 0, 0),
                modifier = Modifier.height(56.dp),
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Refreshing indicator
            if (refreshing) {
                item { LinearProgressIndicator(modifier = Modifier.fillMaxWidth()) }
            }

            // Search bar
            item {
                OutlinedTextField(
                    value = searchQuery,
                    onValueChange = { viewModel.updateSearch(it) },
                    label = { Text("Search series, author, title...") },
                    leadingIcon = { Icon(Icons.Default.Search, null) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
            }

            // Synced-only toggle
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 0.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        text = if (showSyncedOnly) "Synced only" else "All series",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Switch(
                        checked = showSyncedOnly,
                        onCheckedChange = { showSyncedOnly = it },
                    )
                }
            }

            // Sort row
            item {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "${filteredGroups.size} series · ${filteredGroups.sumOf { it.items.size }} items",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.weight(1f),
                    )
                    FilterChip(
                        selected = sortBy == SeriesSort.NAME,
                        onClick = { viewModel.updateSort(SeriesSort.NAME) },
                        label = { Text("A–Z") }
                    )
                    FilterChip(
                        selected = sortBy == SeriesSort.COUNT,
                        onClick = { viewModel.updateSort(SeriesSort.COUNT) },
                        label = { Text("Most Books") }
                    )
                }
            }

            // Series cards
            items(filteredGroups, key = { it.name }) { group ->
                val isExpanded = group.name in expandedSeries
                SeriesCard(
                    group = group,
                    expanded = isExpanded,
                    onToggle = {
                        expandedSeries = if (isExpanded) expandedSeries - group.name
                        else expandedSeries + group.name
                    },
                    onItemClick = { selectedItem = it },
                )
            }

            // Unsorted section
            if (filteredUnsorted.isNotEmpty()) {
                item {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "📁 No Series (${filteredUnsorted.size})",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                items(filteredUnsorted, key = { it.id }) { item ->
                    SeriesItemRow(item = item, showIndex = false, onItemClick = { selectedItem = it })
                }
            }

            // Empty state
            if (filteredGroups.isEmpty() && filteredUnsorted.isEmpty() && !refreshing) {
                item {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(48.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("📚", fontSize = MaterialTheme.typography.displayLarge.fontSize)
                        Spacer(Modifier.height(16.dp))
                        Text(
                            when {
                                searchQuery.isNotBlank() -> "No series matching \"$searchQuery\""
                                showSyncedOnly -> "No synced series"
                                else -> "No books yet"
                            },
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun SeriesCard(
    group: SeriesGroup,
    expanded: Boolean,
    onToggle: () -> Unit,
    onItemClick: (SeriesItem) -> Unit,
) {
    val pairedCount = group.items.count { it.isPaired }
    val ebookOnlyCount = group.items.count { it.hasEbook && !it.hasAudiobook }
    val audioOnlyCount = group.items.count { it.hasAudiobook && !it.hasEbook }

    ElevatedCard(modifier = Modifier.fillMaxWidth()) {
        // Header — always visible
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onToggle() }
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Default.ArrowDropDown,
                contentDescription = if (expanded) "Collapse" else "Expand",
                modifier = Modifier
                    .size(24.dp)
                    .rotate(if (expanded) 0f else -90f),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.width(8.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    group.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                group.author?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            // Badges
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                AssistChip(
                    onClick = onToggle,
                    label = { Text("${group.items.size}") },
                    leadingIcon = { Text("📖") },
                )
                if (pairedCount > 0) {
                    Text("🔗$pairedCount", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                }
                if (ebookOnlyCount > 0) {
                    Text("📚$ebookOnlyCount", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (audioOnlyCount > 0) {
                    Text("🎧$audioOnlyCount", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }

        // Body — expandable
        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically(),
            exit = shrinkVertically(),
        ) {
            Column {
                HorizontalDivider()
                group.items.forEach { item ->
                    SeriesItemRow(item = item, showIndex = true, onItemClick = onItemClick)
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
fun SeriesItemRow(
    item: SeriesItem,
    showIndex: Boolean,
    onItemClick: (SeriesItem) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onItemClick(item) }
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Index badge
        if (showIndex && item.seriesIndex != null) {
            val indexStr = if (item.seriesIndex % 1 == 0f) "#${item.seriesIndex.toInt()}" else "#${item.seriesIndex}"
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = MaterialTheme.shapes.small,
            ) {
                Text(
                    indexStr,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(12.dp))
        } else if (showIndex) {
            Spacer(Modifier.width(44.dp))
        }

        // Title + author
        Column(modifier = Modifier.weight(1f)) {
            Text(item.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            item.author?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        // Format badges
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (item.hasEbook) {
                Surface(
                    color = MaterialTheme.colorScheme.secondaryContainer,
                    shape = MaterialTheme.shapes.small,
                ) {
                    Text(
                        "📚 ${item.ebookFormat?.uppercase() ?: "EPUB"}",
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            if (item.hasAudiobook) {
                Surface(
                    color = MaterialTheme.colorScheme.tertiaryContainer,
                    shape = MaterialTheme.shapes.small,
                ) {
                    Text(
                        "🎧 ${item.audiobookFormat?.uppercase() ?: "M4B"}",
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            if (item.isPaired) {
                Surface(
                    color = MaterialTheme.colorScheme.primaryContainer,
                    shape = MaterialTheme.shapes.small,
                ) {
                    Text(
                        if (item.status == "synced") "✅" else "🔗",
                        modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
    }
}
