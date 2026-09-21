package com.booksync.data.repository

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The process-wide "sync map in use" registry (issue #678) — the reader and
 * [com.booksync.player.AudioPlayerService] register the pair they have open
 * so the library-refresh prune ([SyncMapPruning]) leaves its map alone.
 */
class SyncMapInUseTest {

    @After
    fun tearDown() {
        SyncMapInUse.clearForTest()
    }

    @Test
    fun `a pair is not in use before it is registered`() {
        assertFalse(SyncMapInUse.isInUse(1))
        assertTrue(SyncMapInUse.snapshot().isEmpty())
    }

    @Test
    fun `registering a pair marks it in use`() {
        SyncMapInUse.register(1)
        assertTrue(SyncMapInUse.isInUse(1))
        assertEquals(setOf(1), SyncMapInUse.snapshot())
    }

    @Test
    fun `unregistering clears the mark`() {
        SyncMapInUse.register(1)
        SyncMapInUse.unregister(1)
        assertFalse(SyncMapInUse.isInUse(1))
    }

    @Test
    fun `unregistering a pair that was never registered is a no-op`() {
        SyncMapInUse.unregister(99)
        assertFalse(SyncMapInUse.isInUse(99))
    }

    @Test
    fun `registering twice is idempotent`() {
        SyncMapInUse.register(1)
        SyncMapInUse.register(1)
        assertEquals(setOf(1), SyncMapInUse.snapshot())
    }

    @Test
    fun `several pairs can be in use at once, independently`() {
        SyncMapInUse.register(1)
        SyncMapInUse.register(2)
        SyncMapInUse.unregister(1)

        assertFalse(SyncMapInUse.isInUse(1))
        assertTrue(SyncMapInUse.isInUse(2))
        assertEquals(setOf(2), SyncMapInUse.snapshot())
    }
}
