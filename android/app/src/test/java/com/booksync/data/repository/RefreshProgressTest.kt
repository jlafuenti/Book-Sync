package com.booksync.data.repository

import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.PositionResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Test
import retrofit2.Response

/**
 * [BookSyncRepository.refreshProgress] is the standalone-audiobook counterpart
 * of [BookSyncRepository.refreshBookmark] (issue #162): the bounded pull the
 * resume paths run before seeking. It must obey the same two rules or wiring
 * it in would clobber offline positions:
 *
 *  - never overwrite unsynced local data (offline progress must survive), and
 *  - only pull when the server record is at least as new as the local one.
 */
class RefreshProgressTest {

    private val api = mockk<BookSyncApi>()
    private val userProgressDao = mockk<UserProgressDao>(relaxed = true)

    private fun repository() = buildRepository(
        api = api,
        userProgressDao = userProgressDao,
    )

    private fun serverPosition(
        audioPositionMs: Int = 3_600_000,
        capturedAt: String = "2026-08-20T10:00:00Z",
    ) = Response.success(
        PositionResponse(
            scope = "audiobook",
            book_pair_id = null,
            audiobook_id = 17,
            source = "audiobook",
            anchor_revision = 1,
            audio_position_ms = audioPositionMs,
            updated_at = capturedAt,
            captured_at = capturedAt,
            device_name = "Web · Firefox",
        )
    )

    private fun localProgress(
        audioPositionMs: Int = 1_000,
        capturedAt: String? = "2026-08-10T10:00:00Z",
        synced: Boolean = true,
    ) = UserProgressEntity(scopeKey = TEST_SCOPE, 
        mediaType = "audiobook",
        mediaId = 17,
        bookPairId = null,
        epubCfi = null,
        epubChapter = null,
        epubProgressPercent = null,
        audioPositionMs = audioPositionMs,
        isCompleted = false,
        updatedAt = 1_000L,
        deviceId = "phone",
        capturedAt = capturedAt,
        syncedToServer = synced,
    )

    @Test
    fun `adopts a newer server position written by another device`() = runTest {
        coEvery { api.getPosition("audiobook", 17) } returns serverPosition()
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 17) } returns localProgress()

        val saved = slot<UserProgressEntity>()
        coEvery { userProgressDao.upsertProgress(capture(saved)) } returns Unit

        repository().refreshProgress("audiobook", 17)

        assertEquals(3_600_000, saved.captured.audioPositionMs)
    }

    @Test
    fun `pulls when there is no local row at all`() = runTest {
        coEvery { api.getPosition("audiobook", 17) } returns serverPosition()
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 17) } returns null

        val saved = slot<UserProgressEntity>()
        coEvery { userProgressDao.upsertProgress(capture(saved)) } returns Unit

        repository().refreshProgress("audiobook", 17)

        assertEquals(3_600_000, saved.captured.audioPositionMs)
    }

    @Test
    fun `keeps unsynced local writes instead of clobbering them with the server copy`() = runTest {
        // Offline listening must survive a refresh — regardless of timestamps.
        coEvery { api.getPosition("audiobook", 17) } returns serverPosition()
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 17) } returns
            localProgress(synced = false)

        repository().refreshProgress("audiobook", 17)

        coVerify(exactly = 0) { userProgressDao.upsertProgress(any()) }
    }

    @Test
    fun `keeps a local row that is newer than the server record`() = runTest {
        coEvery { api.getPosition("audiobook", 17) } returns
            serverPosition(capturedAt = "2026-08-01T10:00:00Z")
        coEvery { userProgressDao.getProgress(TEST_SCOPE, "audiobook", 17) } returns
            localProgress(capturedAt = "2026-08-10T10:00:00Z")

        repository().refreshProgress("audiobook", 17)

        coVerify(exactly = 0) { userProgressDao.upsertProgress(any()) }
    }

    @Test
    fun `a 204 (never opened) writes nothing — reads never create`() = runTest {
        coEvery { api.getPosition("audiobook", 17) } returns
            Response.success<PositionResponse>(204, null)

        repository().refreshProgress("audiobook", 17)

        coVerify(exactly = 0) { userProgressDao.upsertProgress(any()) }
    }

    @Test
    fun `an unreachable server writes nothing`() = runTest {
        coEvery { api.getPosition("audiobook", 17) } returns
            Response.error(500, "boom".toResponseBody("text/plain".toMediaType()))

        repository().refreshProgress("audiobook", 17)

        coVerify(exactly = 0) { userProgressDao.upsertProgress(any()) }
    }
}
