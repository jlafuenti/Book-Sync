package com.booksync.data.repository

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The per-device "removed by you" sync-map set (issue #678). See its doc for
 * why this is a separate store rather than reusing `book_pairs.syncMapVersion`.
 *
 * Same mocked-[DataStore] idiom as `AccountViewModelSyncMapSettingsTest`: the
 * mock's `updateData` actually applies the transform to an in-memory
 * [Preferences], so `edit { ... }` behaves like a real DataStore without the
 * cross-thread flakiness a genuine file-backed one has in a fast unit test.
 */
class SyncMapRemovalStoreTest {

    private fun mutableDataStore(initial: Preferences = emptyPreferences()): DataStore<Preferences> {
        var current = initial
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } answers { flowOf(current) }
        coEvery { dataStore.updateData(any()) } coAnswers {
            @Suppress("UNCHECKED_CAST")
            val transform = it.invocation.args[0] as suspend (Preferences) -> Preferences
            current = transform(current)
            current
        }
        return dataStore
    }

    @Test
    fun `a fresh install has nothing removed`() = runBlocking {
        val store = SyncMapRemovalStore(mutableDataStore())
        assertEquals(emptySet<Int>(), store.removedIds().first())
    }

    @Test
    fun `marking a pair removed persists it`() = runBlocking {
        val store = SyncMapRemovalStore(mutableDataStore())

        store.markRemoved(42)

        assertEquals(setOf(42), store.removedIds().first())
    }

    @Test
    fun `marking several pairs at once adds them all`() = runBlocking {
        val store = SyncMapRemovalStore(mutableDataStore())

        store.markRemoved(setOf(1, 2, 3))

        assertEquals(setOf(1, 2, 3), store.removedIds().first())
    }

    @Test
    fun `marking twice is idempotent`() = runBlocking {
        val store = SyncMapRemovalStore(mutableDataStore())

        store.markRemoved(42)
        store.markRemoved(42)

        assertEquals(setOf(42), store.removedIds().first())
    }

    @Test
    fun `clearing a removed pair un-marks only that one`() = runBlocking {
        val store = SyncMapRemovalStore(mutableDataStore())
        store.markRemoved(setOf(1, 2))

        store.clearRemoved(1)

        assertEquals(setOf(2), store.removedIds().first())
    }

    @Test
    fun `clearing a pair that was never marked is a no-op`() = runBlocking {
        val store = SyncMapRemovalStore(mutableDataStore())

        store.clearRemoved(99)

        assertEquals(emptySet<Int>(), store.removedIds().first())
    }

    @Test
    fun `marking an empty set changes nothing`() = runBlocking {
        val dataStore = mutableDataStore()
        val store = SyncMapRemovalStore(dataStore)

        store.markRemoved(emptySet())

        assertEquals(emptySet<Int>(), store.removedIds().first())
    }
}
