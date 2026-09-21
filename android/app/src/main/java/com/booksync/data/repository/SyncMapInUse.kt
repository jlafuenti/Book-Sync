package com.booksync.data.repository

import java.util.concurrent.ConcurrentHashMap

/**
 * Process-wide record of which pairs have their sync map "in use" right now
 * (issue #678): open in the reader, or loaded as the current media item in
 * [com.booksync.player.AudioPlayerService]. The library-refresh prune
 * ([SyncMapPruning.pairsToPrune]) must not drop a streamed pair's map out
 * from under a screen that is reading it at this moment.
 *
 * Deliberately in-memory and unpersisted — it describes "right now, in this
 * process," not a durable preference. A process restart (kill + relaunch) is
 * exactly the case where nothing should still claim to be "in use": the
 * reader and the player both re-register on their own next open.
 *
 * A plain synchronized set would serialize every register/unregister behind
 * one lock; `ConcurrentHashMap.newKeySet()` gives the same "thread-safe set
 * of pair ids" the issue asks for without that contention, and the access
 * pattern here (frequent register/unregister/isInUse checks from the reader
 * activity's lifecycle and the player service's callbacks, both possibly off
 * the main thread) never needs a compound check-then-act.
 */
object SyncMapInUse {
    private val activePairIds = ConcurrentHashMap.newKeySet<Int>()

    /** Mark [pairId]'s sync map as in use. Idempotent. */
    fun register(pairId: Int) {
        activePairIds.add(pairId)
    }

    /** Clear [pairId]'s in-use mark. Safe to call even if it was never registered. */
    fun unregister(pairId: Int) {
        activePairIds.remove(pairId)
    }

    /** Whether [pairId] is currently marked in use. */
    fun isInUse(pairId: Int): Boolean = activePairIds.contains(pairId)

    /** A point-in-time copy of every pair id currently marked in use. */
    fun snapshot(): Set<Int> = activePairIds.toSet()

    /** Test-only: drop every registration so tests don't leak state into each other. */
    internal fun clearForTest() {
        activePairIds.clear()
    }
}
