package com.booksync.ui.player

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModelStore
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.work.WorkManager
import com.booksync.SyncState
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TEST_SCOPE
import com.booksync.player.AudioPlayerService
import com.booksync.player.PlaybackOffsets
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.coVerifyOrder
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import io.mockk.verifyOrder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * The paired player's open-time restore (issue #217; the standalone branch is
 * pinned by [StandalonePositionRestoreTest]).
 *
 * Contract § "Resume paths refresh first": the player pulls the server
 * position (bounded) and the sync map (bounded) *before* it applies the local
 * row, and the restore seek waits for the player to be ready rather than being
 * fired at a player that has nothing loaded. A restore seek is programmatic —
 * it announces itself with `CMD_SUPPRESS_NEXT_SEEK_FLUSH` so the service does
 * not treat it as the user scrubbing and flush it with `claimFormat = true`
 * (issue #166) — while a user seek goes through unsuppressed, because that
 * flush is exactly what persists a scrub while paused.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelRestoreTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        SyncState.pendingAudioSeekMs = -1L
        // The pair listing is not what the restore is about, and a flow that
        // never emits keeps loadAudio (and the android.net.Uri stubs behind it)
        // out of these tests entirely.
        every { repository.getPairsFlow() } returns emptyFlow()
    }

    @After
    fun tearDown() {
        SyncState.pendingAudioSeekMs = -1L
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun bookmark(positionMs: Int) = BookmarkEntity(
        scopeKey = TEST_SCOPE, bookPairId = 42, source = "audiobook",
        epubChapter = 3, epubSentenceIndex = 1, audioPositionMs = positionMs, updatedAt = "1000",
    )

    private fun pairedViewModel() = PlayerViewModel(
        repository = repository,
        appContext = mockk(relaxed = true),
        serverUrlManager = mockk(relaxed = true),
        coverArtHelper = mockk(relaxed = true),
        networkMonitor = mockk(relaxed = true),
        castSessionMonitor = mockk(relaxed = true),
        savedStateHandle = SavedStateHandle(mapOf("pairId" to 42)),
    )

    private fun standaloneViewModel() = PlayerViewModel(
        repository = repository,
        appContext = mockk(relaxed = true),
        serverUrlManager = mockk(relaxed = true),
        coverArtHelper = mockk(relaxed = true),
        networkMonitor = mockk(relaxed = true),
        castSessionMonitor = mockk(relaxed = true),
        savedStateHandle = SavedStateHandle(mapOf("audiobookId" to 17)),
    )

    private fun controller(state: Int): MediaController =
        mockk<MediaController>(relaxed = true).also {
            every { it.isConnected } returns true
            every { it.playbackState } returns state
        }

    /** The real teardown path: ViewModelStore.clear() → ViewModel.clear() → onCleared(). */
    private fun clearViewModel(vm: PlayerViewModel) {
        val store = ViewModelStore()
        store.put("player", vm)
        store.clear()
    }

    // ---- the pull-then-restore ordering ------------------------------------

    @Test
    fun `pulls the server position and the sync map before restoring from the local row`() =
        runTest(dispatcher.scheduler) {
            every { repository.getBookmarkFlow(42) } returns flowOf(bookmark(3_600_000))

            val vm = pairedViewModel()
            advanceUntilIdle()

            coVerifyOrder {
                repository.refreshBookmark(42)
                repository.ensureSyncMapCached(42)
                repository.getBookmarkFlow(42)
            }
            assertEquals(3_600_000L, vm.positionMs.value)
        }

    @Test
    fun `restores from the local row when the server pull hangs`() = runTest(dispatcher.scheduler) {
        // Both pulls are bounded by SERVER_POSITION_TIMEOUT_MS: an unreachable
        // server delays the restore by a moment and must not lose it.
        coEvery { repository.refreshBookmark(42) } coAnswers { awaitCancellation() }
        coEvery { repository.ensureSyncMapCached(42) } coAnswers { awaitCancellation() }
        every { repository.getBookmarkFlow(42) } returns flowOf(bookmark(3_600_000))

        val vm = pairedViewModel()
        advanceUntilIdle()

        assertEquals(3_600_000L, vm.positionMs.value)
    }

    // ---- the restore seek ----------------------------------------------------

    @Test
    fun `a restore seek is announced to the service before it is issued`() = runTest(dispatcher.scheduler) {
        // Found live (SeekFlushWiringTest): reopening the player with its item
        // still loaded meant the restore seek passed the service's has-played
        // gate and was flushed as if the user had scrubbed, claiming the
        // format on a mere screen open.
        every { repository.getBookmarkFlow(42) } returns flowOf(bookmark(3_600_000))
        val ctrl = controller(Player.STATE_READY)
        val vm = pairedViewModel()
        vm.attachControllerForTest(ctrl)

        advanceUntilIdle()

        verifyOrder {
            ctrl.sendCustomCommand(
                match { it.customAction == AudioPlayerService.CMD_SUPPRESS_NEXT_SEEK_FLUSH },
                any(),
            )
            ctrl.seekTo(3_600_000L)
        }
    }

    @Test
    fun `the restore seek waits until the player is ready`() = runTest(dispatcher.scheduler) {
        every { repository.getBookmarkFlow(42) } returns flowOf(bookmark(3_600_000))
        val ctrl = controller(Player.STATE_BUFFERING)
        val vm = pairedViewModel()
        vm.attachControllerForTest(ctrl)

        advanceUntilIdle()

        verify(exactly = 0) { ctrl.seekTo(any<Long>()) }
        // The screen shows the saved point while the seek is parked.
        assertEquals(3_600_000L, vm.positionMs.value)
    }

    @Test
    fun `a later bookmark emission does not re-seek an already restored player`() =
        runTest(dispatcher.scheduler) {
            // Room re-emits on every write — including the player's own
            // heartbeat. Seeking on each of those would yank playback back to
            // wherever the last save landed.
            every { repository.getBookmarkFlow(42) } returns
                flowOf(bookmark(3_600_000), bookmark(3_700_000))
            val ctrl = controller(Player.STATE_READY)
            val vm = pairedViewModel()
            vm.attachControllerForTest(ctrl)

            advanceUntilIdle()

            verify(exactly = 1) { ctrl.seekTo(any<Long>()) }
            verify { ctrl.seekTo(3_600_000L) }
            assertEquals(3_600_000L, vm.positionMs.value)
        }

    @Test
    fun `a position at the very start is not seeked to`() = runTest(dispatcher.scheduler) {
        every { repository.getBookmarkFlow(42) } returns flowOf(bookmark(0))
        val ctrl = controller(Player.STATE_READY)
        val vm = pairedViewModel()
        vm.attachControllerForTest(ctrl)

        advanceUntilIdle()

        verify(exactly = 0) { ctrl.seekTo(any<Long>()) }
        assertEquals(0L, vm.positionMs.value)
    }

    // ---- sentence-sync handoff from the reader ------------------------------

    @Test
    fun `a sentence-sync handoff wins over the stored bookmark and is what teardown persists`() =
        runTest(dispatcher.scheduler) {
            // The reader hands the audio position over in-process precisely
            // because a server round-trip can race the player's own polling.
            SyncState.pendingAudioSeekMs = 250_000L
            every { repository.getBookmarkFlow(42) } returns flowOf(bookmark(3_600_000))
            val ctrl = controller(Player.STATE_READY)
            val vm = pairedViewModel()
            vm.attachControllerForTest(ctrl)

            advanceUntilIdle()

            assertEquals("the handoff is consumed, not left for the next open", -1L, SyncState.pendingAudioSeekMs)
            assertEquals(250_000L, vm.positionMs.value)
            verify(exactly = 0) { ctrl.seekTo(3_600_000L) }

            clearViewModel(vm)
            verify(exactly = 1) {
                repository.savePlaybackPositionDetached(
                    pairId = 42, audioPositionMs = 250_000, appendToLog = false, claimFormat = false,
                )
            }
        }

    @Test
    fun `a standalone handoff skips the server pull the restore would otherwise make`() =
        runTest(dispatcher.scheduler) {
            SyncState.pendingAudioSeekMs = 250_000L
            coEvery { repository.getAudiobookById(17) } returns AudioBookEntity(
                id = 17, title = "t", author = null, filename = "b.m4b", durationSeconds = 7200,
                format = "m4b", series = null, seriesIndex = null,
                uploadedAt = "2026-01-01T00:00:00", isDownloaded = true,
            )
            coEvery { repository.getProgressOnce("audiobook", 17) } returns UserProgressEntity(
                scopeKey = TEST_SCOPE, mediaType = "audiobook", mediaId = 17, bookPairId = null,
                epubCfi = null, epubChapter = null, epubProgressPercent = null,
                audioPositionMs = 3_600_000, isCompleted = false, updatedAt = 1_000L,
                deviceId = "web", capturedAt = "2026-08-20T10:00:00Z", syncedToServer = true,
            )

            val vm = standaloneViewModel()
            advanceUntilIdle()

            coVerify(exactly = 0) { repository.refreshProgress(any(), any()) }
            assertEquals(250_000L, vm.positionMs.value)

            // The write gate is open: the handoff *is* the restore.
            clearViewModel(vm)
            verify(exactly = 1) {
                repository.savePlaybackPositionStandaloneDetached(
                    audiobookId = 17, audioPositionMs = 250_000, claimFormat = false,
                )
            }
        }

    // ---- user seeks ----------------------------------------------------------

    @Test
    fun `a user seek goes straight through, unsuppressed, and writes nothing here`() =
        runTest(dispatcher.scheduler) {
            // The service's onPositionDiscontinuity is the one owner of the
            // seek flush (issue #166). Suppressing it here would silently drop
            // the save for a scrub while paused; saving here would double it.
            val ctrl = controller(Player.STATE_READY)
            val vm = pairedViewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.seekTo(90_000L)

            verify(exactly = 1) { ctrl.seekTo(90_000L) }
            verify(exactly = 0) { ctrl.sendCustomCommand(any(), any()) }
            assertEquals(90_000L, vm.positionMs.value)
            verify(exactly = 0) { repository.savePlaybackPositionDetached(any(), any(), any(), any()) }
        }

    @Test
    fun `skip back and forward are user seeks built from the shared offsets`() =
        runTest(dispatcher.scheduler) {
            val ctrl = controller(Player.STATE_READY)
            every { ctrl.currentPosition } returns 100_000L
            every { ctrl.duration } returns 7_200_000L
            val vm = pairedViewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.skipForward()
            vm.skipBackward()

            verify(exactly = 1) { ctrl.seekTo(PlaybackOffsets.skipForwardPosition(100_000L, 7_200_000L)) }
            verify(exactly = 1) { ctrl.seekTo(PlaybackOffsets.skipBackPosition(100_000L)) }
            verify(exactly = 0) { ctrl.sendCustomCommand(any(), any()) }
        }

    @Test
    fun `a seek with no controller still moves the shown position`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()

        vm.seekTo(5_000L)

        assertEquals(5_000L, vm.positionMs.value)
    }
}
