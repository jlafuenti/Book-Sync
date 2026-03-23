package com.booksync.ui.library

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Book
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity

@Composable
fun NewItemsTab(viewModel: NewItemsViewModel) {
    val newEbooks by viewModel.newEbooks.collectAsState()
    val newAudiobooks by viewModel.newAudiobooks.collectAsState()
    val selectedIds by viewModel.selectedItemIds.collectAsState()

    val totalCount = newEbooks.size + newAudiobooks.size
    val selectedCount = selectedIds.size
    val allSelected = totalCount > 0 && selectedCount == totalCount

    if (totalCount == 0) {
        Box(
            modifier = Modifier.fillMaxSize().padding(32.dp),
            contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("✓", style = MaterialTheme.typography.displayLarge)
                Spacer(Modifier.height(16.dp))
                Text(
                    "No new items",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "New ebooks and audiobooks will appear here when added to Tandem.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        return
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // Bulk action bar
        Surface(tonalElevation = 2.dp) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = allSelected,
                        onCheckedChange = { checked ->
                            if (checked) viewModel.selectAllItems(newEbooks, newAudiobooks)
                            else viewModel.clearItemSelection()
                        }
                    )
                    Spacer(Modifier.width(4.dp))
                    Text(
                        text = if (selectedCount > 0) "$selectedCount selected" else "Select all",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                Button(
                    onClick = { viewModel.acknowledgeSelectedItems() },
                    enabled = selectedCount > 0,
                ) {
                    Text("Acknowledge ($selectedCount)")
                }
            }
        }

        LazyColumn(modifier = Modifier.fillMaxSize()) {
            if (newEbooks.isNotEmpty()) {
                item {
                    ListSectionHeader(
                        text = "Ebooks (${newEbooks.size})",
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                    )
                }
                items(newEbooks, key = { "ebook_${it.id}" }) { ebook ->
                    NewEbookRow(
                        ebook = ebook,
                        selected = (ebook.id to "ebook") in selectedIds,
                        onToggle = { viewModel.toggleItemSelect(ebook.id, "ebook") },
                    )
                    HorizontalDivider(modifier = Modifier.padding(start = 56.dp))
                }
            }

            if (newAudiobooks.isNotEmpty()) {
                item {
                    ListSectionHeader(
                        text = "Audiobooks (${newAudiobooks.size})",
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                    )
                }
                items(newAudiobooks, key = { "audiobook_${it.id}" }) { audiobook ->
                    NewAudiobookRow(
                        audiobook = audiobook,
                        selected = (audiobook.id to "audiobook") in selectedIds,
                        onToggle = { viewModel.toggleItemSelect(audiobook.id, "audiobook") },
                    )
                    HorizontalDivider(modifier = Modifier.padding(start = 56.dp))
                }
            }

            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

@Composable
private fun ListSectionHeader(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier,
    )
}

@Composable
private fun NewEbookRow(
    ebook: EBookEntity,
    selected: Boolean,
    onToggle: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = selected, onCheckedChange = { onToggle() })
        Icon(
            Icons.Default.Book,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = ebook.title,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (ebook.author != null) {
                Text(
                    text = ebook.author,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                text = ebook.filename,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
        SuggestionChip(
            onClick = {},
            label = { Text(ebook.format.uppercase()) },
            modifier = Modifier.height(24.dp),
        )
    }
}

@Composable
private fun NewAudiobookRow(
    audiobook: AudioBookEntity,
    selected: Boolean,
    onToggle: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = selected, onCheckedChange = { onToggle() })
        Icon(
            Icons.Default.Headphones,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.secondary,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = audiobook.title,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (audiobook.author != null) {
                Text(
                    text = audiobook.author,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                text = audiobook.filename,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
        SuggestionChip(
            onClick = {},
            label = { Text(audiobook.format.uppercase()) },
            modifier = Modifier.height(24.dp),
        )
    }
}
