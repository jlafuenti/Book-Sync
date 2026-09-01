package com.booksync.data.remote

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.stringPreferencesKey
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The first launch generates exactly one device id (issue #318).
 *
 * This is the case that decided the whole design. `DeviceIdManager`'s seed does
 * not merely read: when nothing is stored it *generates and persists a UUID*. The
 * obvious way to get the read off the main thread — seed in the background, and
 * let an early reader do its own blocking load — runs that generator twice. Two
 * ids, one persisted, the other cached and handed to callers.
 *
 * That id is what `position_hints` are keyed by and what tells the server which
 * device a reading position came from. Getting it wrong misattributes reading
 * history, and it would only ever misfire on a first launch on a device fast
 * enough to lose the race — which is to say, almost never reproducibly.
 *
 * So: several threads read the id at once, on an empty store, and there must be
 * exactly one value, matching what is on disk.
 */
class DeviceIdFirstLaunchTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val key = stringPreferencesKey("device_id")
    private val seedScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private fun newDataStore(): DataStore<Preferences> =
        PreferenceDataStoreFactory.create(scope = seedScope) {
            tmp.newFile("first-launch-${System.nanoTime()}.preferences_pb")
        }

    @After
    fun tearDown() {
        seedScope.cancel()
    }

    @Test
    fun `concurrent readers on a fresh install see one id, and it is the persisted one`() {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore, seedScope)

        val ready = CountDownLatch(8)
        val go = CountDownLatch(1)
        val seen = java.util.Collections.synchronizedList(mutableListOf<String>())
        val readers = (1..8).map {
            Thread {
                ready.countDown()
                go.await()
                seen += manager.deviceId
            }
        }
        readers.forEach { it.start() }
        ready.await(10, TimeUnit.SECONDS)
        go.countDown()
        readers.forEach { it.join(15_000) }

        assertEquals("every reader must see the same id", 1, seen.toSet().size)

        val persisted = runBlocking { dataStore.data.first()[key] }
        assertNotNull("the id must be persisted, not just cached", persisted)
        assertEquals(
            "the cached id and the persisted id must be the same one",
            persisted, seen.first(),
        )
    }

    @Test
    fun `the id survives a second manager over the same store`() {
        // A process restart reads back what the first launch wrote, rather than
        // minting a replacement and orphaning every position written under the old
        // one.
        val dataStore = newDataStore()

        val first = DeviceIdManager(dataStore, seedScope).deviceId
        val second = DeviceIdManager(dataStore, seedScope).deviceId

        assertTrue("a generated id is not blank", first.isNotBlank())
        assertEquals(first, second)
    }
}
