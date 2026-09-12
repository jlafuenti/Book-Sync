package com.booksync.ui

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for where a cover tap lands (issue #484).
 *
 * `resolvePairOpenTarget` was already correct — it returned `Details` for a pair
 * with nothing on the device, and `ResolvePairOpenTargetTest` proved it. The bug
 * was entirely in the callers: Home folded `Details` in with `Reader`, Search
 * did the same, and Downloaded skipped the resolver altogether and preferred the
 * reader by hand. A perfect resolver with three call sites overriding it is the
 * exact shape of failure a pure-function test cannot see, and all three of these
 * screens are Compose, which Kover excludes.
 *
 * These guards are shallow on purpose — they cannot prove the routing is right,
 * only that the decision is not being taken away from the resolver again.
 */
class CoverTapRoutingWiringTest {

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    private fun codeLines(text: String) =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    /**
     * The specific regression: `Reader, Details ->` in one branch. It read as a
     * tidy merge of two cases that both "open the book", and it meant a tap on a
     * never-opened pair downloaded an EPUB.
     */
    @Test
    fun `Home routes Details to the details screen, not the reader`() {
        val home = codeLines(source("com/booksync/ui/home/HomeScreen.kt"))
        assertTrue(
            "HomeScreen must not fold PairOpenTarget.Details in with Reader — " +
                "the reader fetches the ebook on open, so that fold starts an " +
                "unasked-for download.",
            home.none { it.contains("PairOpenTarget.Reader, PairOpenTarget.Details") },
        )
        assertTrue(
            "Details must reach onOpenPairDetails.",
            home.any { it.contains("PairOpenTarget.Details -> onOpenPairDetails") },
        )
    }

    @Test
    fun `Search routes Details to the pair details screen`() {
        val nav = codeLines(source("com/booksync/ui/BookSyncNavigation.kt"))
        assertTrue(
            "searchDestination must have a Details branch of its own; it used to " +
                "fall through to reader(pairId) on the false premise that no pair " +
                "details route existed.",
            nav.any { it.contains("PairOpenTarget.Details -> bookDetailsPair") },
        )
    }

    /**
     * Downloaded never called the resolver at all, so `bookmarks.source` — the
     * whole mechanism behind "reopen in the format I was using" — did nothing
     * on that tab.
     */
    @Test
    fun `Downloaded asks the resolver instead of preferring the reader`() {
        val screen = codeLines(source("com/booksync/ui/downloaded/DownloadedScreen.kt"))
        assertTrue(
            "DownloadedScreen must consult resolvePairOpenTarget for a card tap.",
            screen.any { it.contains("viewModel.resolvePairOpenTarget(pair)") },
        )
        assertTrue(
            "The hand-rolled `if (pair.ebookDownloaded) …` preference must be gone.",
            screen.none { it.contains("if (pair.ebookDownloaded) onPairBookSelect") },
        )
    }

    /**
     * The resolver needs to know whether a stream is possible. Without this the
     * two axes collapse back into one and a streamed book stops resuming.
     */
    @Test
    fun `the resolver is given the network state`() {
        val repo = codeLines(source("com/booksync/data/repository/LibraryRepository.kt"))
        assertTrue(
            "resolvePairOpenTarget must take isOnline — a claimed audiobook that " +
                "was streamed is openable, and checking it against the download " +
                "flag alone is what sent the listener back to the reader.",
            repo.any { it.contains("fun resolvePairOpenTarget(pair: BookPairEntity, isOnline: Boolean)") },
        )
        assertTrue(
            "The no-claim rungs must stay on the *local* flags, or a first tap " +
                "opens something over the network unasked.",
            repo.any { it.contains("pair.ebookDownloaded                      -> PairOpenTarget.Reader") },
        )
    }
}
