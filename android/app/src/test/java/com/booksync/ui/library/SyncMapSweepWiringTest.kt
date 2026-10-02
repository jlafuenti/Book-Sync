package com.booksync.ui.library

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for where the missing-sync-map sweep lives (issue #786).
 *
 * The sweep that re-fetches a dropped map ran only from `LibraryViewModel.refresh`, which exists
 * once the Library tab has been opened. `LibraryLoader` refreshes (and `refreshPairs` drops a
 * stale map) from Home's `init` too, so on a Home start a dropped map was never re-fetched, and
 * Book Details offered no action for it. The pending-write drain and position pull moved into
 * `LibraryLoader` for the same reason (issue #652). Compose and Hilt wiring are outside what a
 * JVM test can execute, so these pin the shape in the source: one sweep, in the loader, bound in
 * the DI module, and rendered as "Download sync data" on every surface that lists sync actions.
 */
class SyncMapSweepWiringTest {

    private fun source(rel: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$rel")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$rel")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $rel from ${File("").absolutePath}")
    }

    private fun loader() = source("com/booksync/data/repository/LibraryLoader.kt")
    private fun viewModel() = source("com/booksync/ui/library/LibraryViewModel.kt")

    @Test
    fun `the loader runs the sweep after the three fetches, still inside the run`() {
        val src = loader()
        val loaded = src.indexOf("_state.value = LibraryLoadState.Loaded")
        val sweep = src.indexOf("scheduleMissingSyncMaps()", loaded)
        val runEnd = src.indexOf("} catch (e: CancellationException)", loaded)
        assertTrue("could not locate the Loaded write", loaded >= 0)
        assertTrue(
            "runRefresh must call scheduleMissingSyncMaps() after it reaches Loaded, so every " +
                "successful refresh sweeps, whichever screen started it.",
            sweep > loaded && sweep < runEnd,
        )
    }

    @Test
    fun `the loader's sweep uses the shared decision and the hand-removed set`() {
        val src = loader()
        assertTrue(src.contains("SyncMapAutoFetch.pairsToFetch("))
        assertTrue(src.contains("syncMapRemovalStore.removedIds()"))
        assertTrue(src.contains("syncMapFetchScheduler.schedule("))
    }

    @Test
    fun `the view model no longer carries a second copy of the sweep`() {
        val src = viewModel()
        assertFalse("sweep moved to LibraryLoader", src.contains("fun fetchMissingSyncMaps"))
        assertFalse("sweep moved to LibraryLoader", src.contains("SyncMapAutoFetch.pairsToFetch("))
        assertFalse("sweep moved to LibraryLoader", src.contains("fetchMissingSyncMaps(pairs)"))
    }

    @Test
    fun `pruning stays in the view model`() {
        assertTrue(viewModel().contains("pruneUnusedSyncMaps(pairs)"))
    }

    @Test
    fun `the scheduler is bound in the DI module to the WorkManager implementation`() {
        val src = source("com/booksync/di/AppModule.kt")
        assertTrue(src.contains("SyncMapFetchScheduler"))
        assertTrue(src.contains("WorkManagerSyncMapFetchScheduler("))
    }

    @Test
    fun `the WorkManager implementation enqueues the background SYNC_MAP type with KEEP`() {
        val src = source("com/booksync/data/repository/SyncMapFetchScheduler.kt")
        assertTrue(src.contains("DownloadWorker.request(pairId, \"SYNC_MAP\")"))
        assertTrue(src.contains("\"download_sync_\$pairId\""))
        assertTrue(src.contains("ExistingWorkPolicy.KEEP"))
        // Background type, not SYNC_MAP_EXPLICIT: the Wi-Fi-only gate stays in the worker.
        assertFalse(src.contains("request(pairId, \"SYNC_MAP_EXPLICIT\")"))
    }

    // ---- "Download sync data" (issue #786) ----

    @Test
    fun `book details renders Download sync data under offersSyncMapDownload`() {
        val src = source("com/booksync/ui/details/BookDetailsScreen.kt")
        val guard = src.indexOf("if (SyncMapAutoFetch.offersSyncMapDownload(pair))")
        assertTrue("BookDetailsScreen must gate the row on offersSyncMapDownload", guard >= 0)
        val window = src.substring(guard, minOf(src.length, guard + 600))
        assertTrue(window.contains("title = \"Download sync data\""))
        assertTrue(window.contains("Icons.Default.Download"))
        assertTrue(window.contains("viewModel.refreshSyncData()"))
        assertFalse(
            "the new row must not carry a walkthrough anchor",
            window.contains("tourAnchor"),
        )
    }

    @Test
    fun `the card overflow sheet renders Download sync data for the same rule`() {
        val src = source("com/booksync/ui/components/CardOverflowMenu.kt")
        assertTrue(src.contains("PairAction.DownloadSyncData ->"))
        assertTrue(src.contains("\"Download sync data\""))
    }

    @Test
    fun `the downloaded tab no longer hides the sync-data callback when the map is missing`() {
        val src = source("com/booksync/ui/downloaded/DownloadedScreen.kt")
        assertFalse(
            "with the callback null, the sheet cannot show Download sync data for a pair whose " +
                "map is gone; the sheet decides visibility",
            src.contains("onRefreshSyncData  = if (pair.syncMapDownloaded)"),
        )
    }
}
