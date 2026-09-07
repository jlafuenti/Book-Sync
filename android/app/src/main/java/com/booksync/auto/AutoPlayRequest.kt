package com.booksync.auto

import androidx.media3.common.C

/**
 * The decisions on Android Auto's *play* path, as opposed to its browse path
 * (issue #225): what voice search searches, how a result set is paged, and
 * where a play request starts. Pure, so `AudioPlayerService` — excluded from
 * Kover — only wires them to Media3.
 */

/**
 * The whole local library as one flat search index, Continue Listening first
 * (issue #172).
 *
 * That order is load-bearing: `autoSearch` breaks ties in caller order, so
 * "play Bartleby" with two Bartlebys picks the one being listened to, and a
 * blank query — Assistant's "play Tandem" — resumes the most recent book. A
 * book already in [recent] is dropped from [all] so it appears once.
 */
fun autoSearchIndex(recent: List<AutoBook>, all: List<AutoBook>): List<AutoBook> {
    val recentIds = recent.map { it.mediaId }.toSet()
    return recent + all.filterNot { it.mediaId in recentIds }
}

/**
 * One page of search results, in the order they were ranked. Media3's
 * `onGetSearchResult` asks for pages; a page past the end is simply empty.
 */
fun <T> autoSearchPage(all: List<T>, page: Int, pageSize: Int): List<T> {
    val from = (page * pageSize).coerceAtMost(all.size)
    val to = (from + pageSize).coerceAtMost(all.size)
    return all.subList(from, to)
}

/**
 * Google Assistant and Android Auto pass `startIndex = C.INDEX_UNSET` (-1)
 * when they want the player's default. `getOrNull(-1)` is null, which used to
 * lose the bookmarked position embedded in the resolved item's extras and
 * start from 0. Anything out of range means the first item.
 */
fun autoEffectiveStartIndex(startIndex: Int, itemCount: Int): Int =
    if (startIndex < 0 || startIndex >= itemCount) 0 else startIndex

/**
 * Where playback starts: an explicit positive request wins; `C.TIME_UNSET`
 * or zero means "wherever the book was left", which is the resolved item's
 * bookmark.
 */
fun autoStartPositionMs(requestedMs: Long, resumeMs: Long): Long =
    if (requestedMs != C.TIME_UNSET && requestedMs > 0) requestedMs else resumeMs
