package com.booksync.ui.tour

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The two flags behind the offer-once / replay behaviour (issue #597 §3): a
 * fresh install has offered neither, and each mark is independent of the
 * other. Same in-memory-DataStore idiom as `ServerUrlManagerTest`.
 */
class TourPrefsTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private fun newDataStore(): DataStore<Preferences> =
        PreferenceDataStoreFactory.create(scope = scope) {
            tmp.newFile("tour.preferences_pb")
        }

    @After
    fun tearDown() {
        scope.cancel()
    }

    @Test
    fun `fresh install has offered neither flag`() = runBlocking {
        val prefs = TourPrefs(newDataStore())
        assertFalse(prefs.offered.first())
        assertFalse(prefs.completed.first())
    }

    @Test
    fun `markOffered sets offered without touching completed`() = runBlocking {
        val prefs = TourPrefs(newDataStore())
        prefs.markOffered()
        assertTrue(prefs.offered.first())
        assertFalse(prefs.completed.first())
    }

    @Test
    fun `markCompleted sets completed without touching offered`() = runBlocking {
        val prefs = TourPrefs(newDataStore())
        prefs.markCompleted()
        assertTrue(prefs.completed.first())
        assertFalse(prefs.offered.first())
    }

    @Test
    fun `flags persist across TourPrefs instances over the same store`() = runBlocking {
        val dataStore = newDataStore()
        TourPrefs(dataStore).markOffered()
        assertTrue(TourPrefs(dataStore).offered.first())
    }
}
