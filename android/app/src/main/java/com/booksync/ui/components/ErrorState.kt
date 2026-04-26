package com.booksync.ui.components

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * Error-flavored empty state. Uses the error status tone via the warning icon.
 * Delegates layout to [EmptyState] so empty / error states stay visually aligned.
 */
@Composable
fun ErrorState(
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    retryLabel: String? = "Try again",
    onRetry: (() -> Unit)? = null,
) {
    EmptyState(
        icon = Icons.Default.ErrorOutline,
        title = title,
        subtitle = message,
        modifier = modifier,
        actionLabel = if (onRetry != null) retryLabel else null,
        onAction = onRetry,
    )
}
