package com.booksync.ui.player

import com.booksync.data.repository.TEST_SCOPE

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModelStore
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.BookSyncRepository
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * Regression tests for issue #165: `ViewModel.clear()` cancels viewModelScope
 * BEFORE invoking `onCleared()`, so the final "stop event" save that
 * `onCleared` used to `viewModelScope.launch` was dead on arrival — its body
 * never ran. What was actually lost is anything that changed after the last
 * pause, in particular a seek/skip while paused (issue #166).
 *
 * The teardown save must go through the repository's detached helpers
 * (issue #164), which run on the app scope under NonCancellable.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelTeardownTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun clearViewModel(vm: PlayerViewModel) {
        // The real teardown path: ViewModelStore.clear() → ViewModel.clear()
        // (closes viewModelScope) → onCleared().
        val store = ViewModelStore()
        store.put("player", vm)
        store.clear()
    }

    @Test
    fun `clearing a paired player still issues the final save`() = runTest(dispatcher.scheduler) {
        val vm = PlayerViewModel(
            repository = repository,
            appContext = mockk(relaxed = true),
            savedStateHandle = SavedStateHandle(mapOf("pairId" to 42)),
        )
        advanceUntilIdle()

        clearViewModel(vm)

        verify(exactly = 1) {
            repository.savePlaybackPositionDetached(
                pairId = 42, audioPositionMs = any(), appendToLog = true, claimFormat = any())
        }
    }

    @Test
    fun `clearing a standalone player still issues the final save`() = runTest(dispatcher.scheduler) {
        coEvery { repository.getAudiobookById(17) } returns AudioBookEntity(
            id = 17, title = "t", author = null, filename = "b.m4b", durationSeconds = 7200,
            format = "m4b", series = null, seriesIndex = null,
            uploadedAt = "2026-01-01T00:00:00", isDownloaded = true,
        )
        coEvery { repository.getProgressOnce("audiobook", 17) } returns UserProgressEntity(scopeKey = TEST_SCOPE, 
            mediaType = "audiobook", mediaId = 17, bookPairId = null, epubCfi = null,
            epubChapter = null, epubProgressPercent = null, audioPositionMs = 3_600_000,
            isCompleted = false, updatedAt = 1_000L, deviceId = "web",
            capturedAt = "2026-08-20T10:00:00Z", syncedToServer = true,
        )

        val vm = PlayerViewModel(
            repository = repository,
            appContext = mockk(relaxed = true),
            savedStateHandle = SavedStateHandle(mapOf("audiobookId" to 17)),
        )
        advanceUntilIdle() // restore completes; the write gate is open

        clearViewModel(vm)

        // The restored position — not 0 — reaches the detached save.
        verify(exactly = 1) {
            repository.savePlaybackPositionStandaloneDetached(
                audiobookId = 17, audioPositionMs = 3_600_000, claimFormat = any())
        }
    }
}
