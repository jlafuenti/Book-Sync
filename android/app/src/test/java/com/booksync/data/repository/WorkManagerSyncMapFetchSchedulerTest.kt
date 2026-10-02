package com.booksync.data.repository

import android.content.Context
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * The WorkManager-backed [SyncMapFetchScheduler] (issue #786) must enqueue exactly what the
 * sweep always enqueued from `LibraryViewModel`: the background `SYNC_MAP` download, under the
 * pair's own unique name, with `KEEP` so a fetch already in flight is not restarted by the
 * next refresh. The Wi-Fi-only gate is applied inside the worker, not here.
 */
class WorkManagerSyncMapFetchSchedulerTest {

    private val workManager = mockk<WorkManager>(relaxed = true)
    private val context = mockk<Context>(relaxed = true)

    @Before
    fun setUp() {
        // WorkManager.getInstance is a Kotlin companion function; same idiom as SearchDownloadTest.
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns workManager
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
    }

    @Test
    fun `schedule enqueues one unique KEEP work item named for the pair`() {
        WorkManagerSyncMapFetchScheduler(context).schedule(42)

        verify(exactly = 1) {
            workManager.enqueueUniqueWork(
                "download_sync_42",
                ExistingWorkPolicy.KEEP,
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `each pair gets its own unique name`() {
        val scheduler = WorkManagerSyncMapFetchScheduler(context)

        scheduler.schedule(1)
        scheduler.schedule(2)

        verify(exactly = 1) {
            workManager.enqueueUniqueWork("download_sync_1", ExistingWorkPolicy.KEEP, any<OneTimeWorkRequest>())
        }
        verify(exactly = 1) {
            workManager.enqueueUniqueWork("download_sync_2", ExistingWorkPolicy.KEEP, any<OneTimeWorkRequest>())
        }
    }
}
