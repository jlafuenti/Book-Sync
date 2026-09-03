package com.booksync.ui.downloaded

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * "Mark Complete" on a pair goes through [BookSyncRepository.markPairComplete]
 * — one pair-scoped write — not two standalone-scope `markComplete` calls
 * (issue #56, docs/position-sync-contract.md § Completion). Every screen that
 * offers the action on a pair (Home, Library, Downloaded, Book Details, the
 * player and reader overflow menus) is wired the same way; this pins one of
 * them so the pattern can't silently regress to the split write.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DownloadedViewModelMarkCompleteTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        // WorkManager.getInstance is a Kotlin companion function, so the
        // companion object (not a Java static) is what needs mocking.
        mockkObject(WorkManager.Companion)
        val workManager = mockk<WorkManager>(relaxed = true)
        every { workManager.getWorkInfosByTagFlow(any()) } returns emptyFlow()
        every { WorkManager.getInstance(any<Context>()) } returns workManager
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun newViewModel(): DownloadedViewModel {
        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        return DownloadedViewModel(
            repository = repository,
            serverUrlManager = serverUrlManager,
            tokenManager = mockk(relaxed = true),
            context = mockk(relaxed = true),
        )
    }

    private fun pair() = BookPairEntity(
        id = 7,
        ebookId = 100, ebookTitle = "E", ebookAuthor = null, ebookFilename = "e.epub", ebookFormat = "epub",
        audiobookId = 200, audiobookTitle = "A", audiobookAuthor = null, audiobookFilename = "a.m4b",
        audiobookFormat = "m4b", audiobookDurationSeconds = null, status = "synced",
    )

    @Test
    fun `marking a pair complete is one pair-scoped repository call`() {
        newViewModel().markComplete(pair())

        coVerify(exactly = 1) { repository.markPairComplete(7, 100, 200) }
        coVerify(exactly = 0) { repository.markComplete(any(), any()) }
    }
}
