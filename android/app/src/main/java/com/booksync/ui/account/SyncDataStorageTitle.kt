package com.booksync.ui.account

/**
 * The Account → Storage "Sync data" line (issue #678). Outside the Composable
 * so it can carry a test — Compose is excluded from Kover.
 *
 * Whole megabytes only: the byte count is itself an estimate (see
 * [com.booksync.data.local.dao.SyncMapStorageStats]), so a decimal would claim
 * precision it doesn't have. Under half a megabyte reads as a bound, "under
 * 1 MB", rather than "0 MB", which would look broken with data present.
 */
fun syncDataStorageTitle(pairCount: Int, approxBytes: Long): String {
    if (pairCount == 0) return "Sync data — none"
    val books = if (pairCount == 1) "1 book" else "$pairCount books"
    val mb = approxBytes / (1024.0 * 1024.0)
    val size = if (mb < 0.5) "under 1 MB" else "about ${Math.round(mb)} MB"
    return "Sync data — $books, $size"
}
