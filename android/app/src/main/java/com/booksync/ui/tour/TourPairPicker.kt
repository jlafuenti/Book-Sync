package com.booksync.ui.tour

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.LibraryRepository
import com.booksync.data.util.NetworkMonitor
import kotlinx.coroutines.flow.first
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Pure decision behind [TourPairPicker.pick] (issue #597 §3): first a synced
 * pair with something already on the device (so the walkthrough's reader/
 * player steps don't depend on the network at all), else any synced pair as
 * long as we're online (it can stream), else null — a library with nothing
 * ready yet, which [TourController] turns into one explained skip card
 * rather than quietly dropping the pair-dependent steps.
 */
fun choose(pairs: List<BookPairEntity>, isOnline: Boolean): Int? {
    val synced = pairs.filter { it.status == "synced" }
    if (synced.isEmpty()) return null
    val withLocal = synced.firstOrNull { it.ebookDownloaded || it.audiobookDownloaded }
    if (withLocal != null) return withLocal.id
    return if (isOnline) synced.first().id else null
}

/**
 * Whether [pair] is exactly as the walkthrough found it: no progress, and nothing pulled
 * onto the device on its account (issue #597 tester feedback — the welcome card promised no
 * trace, but a book downloaded or read during the tour kept showing up in Continue Reading
 * and Downloaded after). True is what makes [pair] a candidate for [TourController] to fully
 * clean up when the tour finishes or is quit — untouched going in is how it should come back
 * out too.
 */
fun pairIsUntouched(pair: BookPairEntity, hasProgress: Boolean): Boolean =
    // A cached sync map on its own is invisible to the user (no card, no list, no
    // position) and is refetched on demand, so it does not make a pair "touched";
    // cleanup still clears it along with everything else.
    !hasProgress && !pair.ebookDownloaded && !pair.audiobookDownloaded

/** Reads the library and connectivity to answer [choose] for [TourController.start]. */
@Singleton
class TourPairPicker @Inject constructor(
    private val bookPairDao: BookPairDao,
    private val networkMonitor: NetworkMonitor,
    private val libraryRepository: LibraryRepository,
) {
    suspend fun pick(): Int? {
        val pairs = bookPairDao.getAllPairs().first()
        return choose(pairs, networkMonitor.isOnline.value)
    }

    /**
     * [pairIsUntouched] for [pairId], read fresh rather than trusted from whatever [pick] saw
     * a moment earlier — [TourController] calls this again from `adoptPair` for a pair the
     * Library substituted after [pick] already ran. False for an id that no longer resolves
     * to a pair.
     */
    suspend fun isUntouched(pairId: Int): Boolean {
        val pair = bookPairDao.getPairById(pairId) ?: return false
        val summary = libraryRepository.progressSummaryForPair(pair)
        return pairIsUntouched(pair, summary.hasProgress)
    }
}
