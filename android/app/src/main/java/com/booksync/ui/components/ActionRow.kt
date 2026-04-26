package com.booksync.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.ui.theme.Tandem

/**
 * Tappable settings-style row with a title, optional description, and a trailing icon.
 * Used by Account, Book Details, and anywhere else we present a vertical action list.
 *
 * Set [destructive] = true to tint the title red (for "Delete", "Unlink", etc.).
 * Set [enabled] = false to grey the row out and suppress clicks.
 */
@Composable
fun ActionRow(
    title: String,
    description: String? = null,
    destructive: Boolean = false,
    enabled: Boolean = true,
    trailingIcon: ImageVector = Icons.Default.ChevronRight,
    onClick: () -> Unit,
) {
    val colors = Tandem.colors
    val tint = when {
        !enabled     -> colors.textMuted
        destructive  -> colors.statusError
        else         -> colors.textPrimary
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, color = tint, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            if (description != null) {
                Text(description, color = colors.textSecondary, fontSize = 12.sp)
            }
        }
        Icon(trailingIcon, contentDescription = null, tint = colors.textMuted, modifier = Modifier.size(18.dp))
    }
}
