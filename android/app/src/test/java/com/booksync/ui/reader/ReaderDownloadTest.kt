package com.booksync.ui.reader

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import java.io.File
import java.net.UnknownHostException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * "Download Ebook" on the reader placeholder must work on a cold cache, and
 * must never fail in silence (issue #417).
 *
 * The reader learns about its pair from `getPairsFlow()`, which is Room. A
 * search result comes from the server, so on a fresh install — Library tab
 * never opened — tapping a paired result lands on a reader whose `pair` is
 * null. `downloadEbook()` began with `_pair.value ?: return`, so the button
 * did nothing: no request, no snackbar, not even a logcat line. Same defect
 * class as issue #338, which fixed the Search screen's download menu but not
 * this button.
 *
 * Same two halves as `SearchDownloadTest`: the pair is resolved (and cached)
 * before any work is enqueued, and every way that can fail ends in a message
 * on screen.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ReaderDownloadTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)
    private val workInfos = MutableStateFlow<List<WorkInfo>>(emptyList())

    /** What Room holds. Empty is the cold cache the issue describes. */
    private val cachedPairs = MutableStateFlow<List<BookPairEntity>>(emptyList())

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { workManager.getWorkInfosByTagFlow(any()) } returns workInfos
        every { WorkManager.getInstance(any<Context>()) } returns workManager
        every { repository.getPairsFlow() } returns cachedPairs
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun newViewModel(pairId: Int = 7): ReaderViewModel = ReaderViewModel(
        repository = repository,
        savedStateHandle = SavedStateHandle(mapOf("pairId" to pairId)),
        context = mockk(relaxed = true),
    )

    private val pair = BookPairEntity(
        id = 7, ebookId = 42, ebookTitle = "Bartleby", ebookAuthor = null,
        ebookFilename = "b.epub", ebookFormat = "epub",
        audiobookId = 1516, audiobookTitle = "Bartleby", audiobookAuthor = null,
        audiobookFilename = "b.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 60, status = "synced",
    )

    // --- The happy path a cold cache used to fail --------------------------

    @Test
    fun `a pair missing from the cache is resolved before the ebook download is enqueued`() {
        coEvery { repository.resolvePairById(7) } returns pair

        newViewModel().downloadEbook()

        coVerify(exactly = 1) { repository.resolvePairById(7) }
        verify(exactly = 1) {
            workManager.enqueueUniqueWork(
                "download_ebook_7",
                ExistingWorkPolicy.REPLACE,
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `the refresh that fills the cache does not start a second download`() {
        // resolvePairById refreshes the pair list into Room, which re-emits on
        // getPairsFlow. The auto-download guard (issue #171) must treat the
        // explicit tap as "already requested" so that emission enqueues nothing.
        coEvery { repository.resolvePairById(7) } answers {
            cachedPairs.value = listOf(pair)
            pair
        }

        newViewModel().downloadEbook()

        verify(exactly = 1) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }

    @Test
    fun `a pair the cache already has is not fetched again`() {
        cachedPairs.value = listOf(pair)

        newViewModel().downloadEbook()

        coVerify(exactly = 0) { repository.resolvePairById(any()) }
        verify(atLeast = 1) {
            workManager.enqueueUniqueWork(
                "download_ebook_7",
                ExistingWorkPolicy.REPLACE,
                any<OneTimeWorkRequest>(),
            )
        }
    }

    // --- Failures the user can see -----------------------------------------

    @Test
    fun `an unreachable server shows an error and enqueues nothing`() {
        coEvery { repository.resolvePairById(7) } throws UnknownHostException("no such host")

        val vm = newViewModel()
        vm.downloadEbook()

        val message = vm.downloadError.value
        assertNotNull("a failed tap must not be silent", message)
        assertTrue("must blame the network, was: $message", message!!.contains("reach the server"))
        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }

    @Test
    fun `a pair the server no longer has shows an error and enqueues nothing`() {
        coEvery { repository.resolvePairById(7) } returns null

        val vm = newViewModel()
        vm.downloadEbook()

        val message = vm.downloadError.value
        assertNotNull("a failed tap must not be silent", message)
        assertTrue("must say the server no longer has it, was: $message", message!!.contains("server"))
        assertTrue("must not leak a null title, was: $message", !message.contains("null"))
        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }

    @Test
    fun `dismissing the error clears it`() {
        coEvery { repository.resolvePairById(7) } returns null
        val vm = newViewModel()
        vm.downloadEbook()
        assertNotNull(vm.downloadError.value)

        vm.clearDownloadError()

        assertNull(vm.downloadError.value)
    }

    // --- Source guard --------------------------------------------------------

    @Test
    fun `every download the reader enqueues goes through the resolver`() {
        // The bug was an enqueue guarded by `_pair.value ?: return`, i.e. one
        // that trusted Room to already hold the row. Any enqueue under
        // ui/reader must sit in a function that resolves cache-then-server
        // first, so the next entry point added here cannot regress the same
        // way Search (#338) and the reader placeholder (#417) did.
        val enqueueCall = Regex("""\.enqueue(?:Unique)?Work\(|\.enqueue\(""")
        val resolver = Regex("""resolve(?:Pair|Ebook|Audiobook)ById\(""")
        val offenders = mutableListOf<String>()

        for (file in readerSources()) {
            val lines = file.readLines()
            lines.forEachIndexed { index, line ->
                if (!enqueueCall.containsMatchIn(line)) return@forEachIndexed
                // The enclosing function is the nearest `fun` above the call.
                val start = (index downTo 0).firstOrNull { lines[it].trimStart().startsWith("fun ") || lines[it].contains(" fun ") }
                    ?: 0
                val body = lines.subList(start, index + 1).joinToString("\n")
                if (!resolver.containsMatchIn(body)) {
                    offenders += "${file.name}:${index + 1}: ${line.trim()}"
                }
            }
        }

        assertTrue(
            "these enqueue a download without resolving the row from cache-then-server first:\n" +
                offenders.joinToString("\n"),
            offenders.isEmpty(),
        )
    }

    private fun readerSources(): List<File> {
        // Gradle runs unit tests with the module directory as the working dir;
        // walking upward also covers being run from `android/`.
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(
                File(dir, "app/src/main/java/com/booksync/ui/reader"),
                File(dir, "src/main/java/com/booksync/ui/reader"),
            )) {
                if (candidate.isDirectory) {
                    return candidate.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
                }
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate ui/reader sources from ${File("").absolutePath}")
    }
}
