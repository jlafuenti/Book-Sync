package com.booksync.ui.components

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.booksync.R
import com.booksync.data.remote.SUPPORTED_API_VERSION
import com.booksync.data.remote.VersionBanner

/**
 * The client/server version warning (issue #174).
 *
 * **Non-blocking, and shown only on a real mismatch.** The app keeps working
 * against a server one version out in either direction; the point is that when
 * something does break, whoever can fix it has already been told which side is
 * stale, instead of meeting a 404 or a parse error with no explanation.
 *
 * Renders nothing for null — which is what agreement, an old server that reports
 * no version, and an unreachable server all produce. See [VersionBanner].
 */
@Composable
fun VersionMismatchBanner(banner: VersionBanner?, modifier: Modifier = Modifier) {
    if (banner == null) return

    val text = when (banner) {
        VersionBanner.SERVER_NEWER -> stringResource(R.string.version_server_newer)
        VersionBanner.SERVER_OLDER ->
            stringResource(R.string.version_server_older, SUPPORTED_API_VERSION)
    }

    Card(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
            contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        ),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = Icons.Default.Info,
                contentDescription = null,
                modifier = Modifier.size(20.dp),
            )
            Text(
                text = text,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(start = 12.dp),
            )
        }
    }
}
