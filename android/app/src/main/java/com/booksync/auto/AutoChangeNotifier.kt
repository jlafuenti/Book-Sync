package com.booksync.auto

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop

/**
 * Turns a flow of full snapshots — e.g. `LibraryRepository.getRecentlyPlayedPairsFlow()`
 * — into a flow of *changes only*: the shape `MediaLibrarySession.notifyChildrenChanged`
 * needs (issue #583).
 *
 * `AudioPlayerService` never called `notifyChildrenChanged`, so Android Auto's
 * Continue Listening tab only ever refreshed when the head unit re-subscribed
 * (an app switch or reconnect) rather than when the underlying data changed
 * under it. The fix observes the same Room flows a browse node is built from
 * and notifies on every emission that is a genuine change — which this
 * function decides, so the "skip the initial emission, collapse duplicates"
 * rule lives somewhere a JVM test can hold it. `AudioPlayerService` needs a
 * `MediaLibrarySession` and is excluded from Kover for exactly that reason
 * (see `AutoWiringTest`), so Flow logic written directly in the service would
 * be untested.
 *
 * [distinctUntilChanged] runs first, collapsing any run of emissions that
 * carry the same value — an unrelated write to a watched table can re-run a
 * Room query and re-emit an identical list, which must not fire a notify.
 * [drop]\(1\) then removes the first *distinct* value: that one is the tab's
 * initial load, not a change a subscribed browser needs to hear about. Doing
 * this in the other order — dropping the first raw emission, then
 * deduplicating — would let a flow that starts with a run of duplicates (A,
 * A, B) misjudge the initial value: the first two `A`s are the same load
 * repeated, not one load followed by nothing changing.
 */
fun <T> Flow<T>.changesToNotify(): Flow<T> = distinctUntilChanged().drop(1)
