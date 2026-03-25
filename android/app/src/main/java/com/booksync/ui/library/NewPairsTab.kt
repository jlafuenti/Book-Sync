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
import com.booksync.data.local.entity.BookPairEntity

@Composable
fun NewPairsTab(viewModel: NewItemsViewModel) {
    val newPairs by viewModel.newPairs.collectAsState()
    val selectedIds by viewModel.selectedPairIds.collectAsState()

    val totalCount = newPairs.size
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
                    "No new pairs",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "New pairs will appear here for metadata review.",
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
                            if (checked) viewModel.selectAllPairs(newPairs)
                            else viewModel.clearPairSelection()
                        }
                    )
                    Spacer(Modifier.width(4.dp))
                    Text(
                        text = if (selectedCount > 0) "$selectedCount selected" else "Select all",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                Button(
                    onClick = { viewModel.acknowledgeSelectedPairs() },
                    enabled = selectedCount > 0,
                ) {
                    Text("Acknowledge ($selectedCount)")
                }
            }
        }

        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(newPairs, key = { it.id }) { pair ->
                NewPairCard(
                    pair = pair,
                    selected = pair.id in selectedIds,
                    onToggleSelect = { viewModel.togglePairSelect(pair.id) },
                    onSkipMismatches = { viewModel.skipAllMismatches(pair.id) },
                )
                HorizontalDivider()
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

@Composable
private fun NewPairCard(
    pair: BookPairEntity,
    selected: Boolean,
    onToggleSelect: () -> Unit,
    onSkipMismatches: () -> Unit,
) {
    val mismatches = pair.detectMismatches()

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Checkbox(
                checked = selected,
                onCheckedChange = { onToggleSelect() },
                modifier = Modifier.padding(top = 2.dp),
            )
            Spacer(Modifier.width(8.dp))
            Column(modifier = Modifier.weight(1f)) {
                // Ebook side
                PairSideRow(
                    icon = { Icon(Icons.Default.Book, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(16.dp)) },
                    label = "Ebook",
                    title = pair.ebookTitle,
                    author = pair.ebookAuthor,
                    filename = pair.ebookFilename,
                    titleMismatch = mismatches.any { it.field == "Title" },
                    authorMismatch = mismatches.any { it.field == "Author" },
                    filenameMismatch = mismatches.any { it.field == "Ebook filename" },
                )

                Spacer(Modifier.height(8.dp))
                HorizontalDivider(thickness = 0.5.dp)
                Spacer(Modifier.height(8.dp))

                // Audiobook side
                PairSideRow(
                    icon = { Icon(Icons.Default.Headphones, null, tint = MaterialTheme.colorScheme.secondary, modifier = Modifier.size(16.dp)) },
                    label = "Audiobook",
                    title = pair.audiobookTitle,
                    author = pair.audiobookAuthor,
                    filename = pair.audiobookFilename,
                    titleMismatch = mismatches.any { it.field == "Title" },
                    authorMismatch = mismatches.any { it.field == "Author" },
                    filenameMismatch = mismatches.any { it.field == "Audiobook filename" },
                )

                if (mismatches.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.End,
                    ) {
                        OutlinedButton(
                            onClick = onSkipMismatches,
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                        ) {
                            Text(
                                "Skip ${mismatches.size} mismatch${if (mismatches.size != 1) "es" else ""}",
                                style = MaterialTheme.typography.labelMedium,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PairSideRow(
    icon: @Composable () -> Unit,
    label: String,
    title: String,
    author: String?,
    filename: String,
    titleMismatch: Boolean,
    authorMismatch: Boolean,
    filenameMismatch: Boolean,
) {
    Row(verticalAlignment = Alignment.Top) {
        icon()
        Spacer(Modifier.width(8.dp))
        Column {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            MismatchableText(
                text = title,
                hasMismatch = titleMismatch,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
            if (author != null) {
                MismatchableText(
                    text = author,
                    hasMismatch = authorMismatch,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            MismatchableText(
                text = filename,
                hasMismatch = filenameMismatch,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun MismatchableText(
    text: String,
    hasMismatch: Boolean,
    style: androidx.compose.ui.text.TextStyle,
    fontWeight: FontWeight? = null,
) {
    Text(
        text = text,
        style = style,
        fontWeight = fontWeight,
        color = if (hasMismatch) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
    )
}
