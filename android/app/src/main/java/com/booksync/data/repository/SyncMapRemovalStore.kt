package com.booksync.data.repository

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringSetPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Per-device DataStore key for the pair ids the #537 sweep must leave alone
 * (issue #678) — same file/pattern as `AUTO_CLEANUP_EBOOKS` etc. in
 * `ui/account/AccountViewModel.kt`, but this one needs to be read from
 * outside the Account screen ([com.booksync.ui.library.LibraryViewModel]'s
 * sweep, [MediaDownloadRepository]'s download path), so it lives in its own
 * small store rather than as a bare key + raw `DataStore<Preferences>` inject
 * at each call site.
 */
private val SYNC_MAP_REMOVED_IDS = stringSetPreferencesKey("sync_map_removed_ids")

/**
 * Tracks pairs whose sync map the user removed by hand — "Remove sync data"
 * (Book Details / card overflow menu) and "Clear" (Account → Storage) —
 * issue #678.
 *
 * Without this, `SyncMapAutoFetch`'s #537 sweep would treat a hand-removed
 * map exactly like one that was never fetched, and re-download it on the
 * very next library refresh: the sweep only looks at `book_pairs` and
 * `sync_points`, neither of which records *why* a map is missing.
 *
 * Deliberately **not** `book_pairs.syncMapVersion`: a reader/player position
 * save attests that column to the server as the sync-map version the write
 * was resolved against (issue #116). Repurposing it as a "removed" marker
 * would corrupt that attestation for the next save. This is a separate,
 * purely local, per-device set that never leaves the device and is never
 * read by anything that talks to the server.
 *
 * The mark is cleared by [clearRemoved] — called after a `DownloadWorker` run
 * that fetches the sync map ([MediaDownloadRepository.downloadSyncMap], the
 * single place every download path and the explicit "Refresh sync data"
 * button funnel through) — so downloading the book again, or asking for the
 * data explicitly, un-does the removal.
 */
@Singleton
class SyncMapRemovalStore @Inject constructor(
    private val dataStore: DataStore<Preferences>,
) {
    /** Pair ids currently marked "removed by you". */
    fun removedIds(): Flow<Set<Int>> =
        dataStore.data.map { prefs ->
            prefs[SYNC_MAP_REMOVED_IDS]?.mapNotNull { it.toIntOrNull() }?.toSet() ?: emptySet()
        }

    /** Mark one pair as removed by hand. */
    suspend fun markRemoved(pairId: Int) = markRemoved(setOf(pairId))

    /** Mark several pairs as removed by hand in one write — used by "Clear all". */
    suspend fun markRemoved(pairIds: Set<Int>) {
        if (pairIds.isEmpty()) return
        dataStore.edit { prefs ->
            val current = prefs[SYNC_MAP_REMOVED_IDS] ?: emptySet()
            prefs[SYNC_MAP_REMOVED_IDS] = current + pairIds.map { it.toString() }
        }
    }

    /** Un-mark one pair — a fresh download or "Refresh sync data" for it. */
    suspend fun clearRemoved(pairId: Int) {
        dataStore.edit { prefs ->
            val current = prefs[SYNC_MAP_REMOVED_IDS] ?: emptySet()
            val idString = pairId.toString()
            if (idString in current) {
                prefs[SYNC_MAP_REMOVED_IDS] = current - idString
            }
        }
    }
}
