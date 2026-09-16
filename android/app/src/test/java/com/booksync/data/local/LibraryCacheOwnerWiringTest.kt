package com.booksync.data.local

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * That the owner check of issue #575 is actually *reached* before the library
 * cache is read.
 *
 * This file exists because of the shape of issue #573: the safety step was
 * written, tested and shipped, and the only route to it was one nothing took.
 * A `LibraryCacheOwner` that is correct and never called is the same bug again.
 *
 * The call sites that matter are the Android Auto ones. `onAuthenticated()`
 * covers the phone — `LoginScreen`, `DemoSignIn` and `BookSyncNavigation`'s
 * launch effect all await it before a screen composes — but **none of them run
 * in the car**. `AudioPlayerService` is a `MediaLibraryService`, and Android
 * Auto and system media resumption start it with no Activity alive, so the
 * browse tree, the voice index and a play-by-media-id can all read Room in a
 * process where no login path has ever executed. `ScopeAdoptionDao` carries the
 * same warning for issue #314.
 *
 * `AudioPlayerService` needs a `MediaLibrarySession` and a head unit and is
 * excluded from Kover, so this reads the source — the `AutoWiringTest` /
 * `SyncWiringTest` precedent.
 */
class LibraryCacheOwnerWiringTest {

    private fun repoFile(relativePath: String): File {
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(File(dir, "app/$relativePath"), File(dir, relativePath))) {
                if (candidate.exists()) return candidate
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    private fun source(path: String) = repoFile("src/main/java/$path").readText()

    private val service by lazy { source("com/booksync/player/AudioPlayerService.kt") }
    private val scopeProvider by lazy { source("com/booksync/data/remote/UserScopeProvider.kt") }
    private val tokenManager by lazy { source("com/booksync/data/remote/TokenManager.kt") }

    /** From the declaration of [name] to the start of the next top-level-ish `fun`. */
    private fun functionBody(text: String, name: String): String {
        val lines = text.lines()
        val start = lines.indexOfFirst { Regex("fun $name\\b").containsMatchIn(it) }
        if (start < 0) throw AssertionError("No function named $name")
        val end = lines.drop(start + 1).indexOfFirst {
            Regex("""^\s{0,8}(private |internal |public |override )*(suspend )?fun \w""")
                .containsMatchIn(it)
        }
        return lines.subList(start, if (end < 0) lines.size else start + 1 + end)
            .filter { !it.trim().startsWith("//") && !it.trim().startsWith("*") }
            .joinToString("\n")
    }

    /**
     * Every Auto entry point that reads the library cache, and the guard each
     * one has to run first. These three are where the car reaches Room: the
     * browse tree, the voice-search index (shared by `onSearch` and
     * "play X on Tandem"), and resolving a media id to something playable —
     * which is the path the issue's downloaded book took.
     */
    private val cacheReaders = listOf("childrenOf", "loadAutoSearchIndex", "resolveMediaItem")

    @Test
    fun `every Auto cache reader checks the owner before it touches the repository`() {
        for (name in cacheReaders) {
            val body = functionBody(service, name)
            val guard = body.indexOf("libraryCacheReadable()")
            val read = body.indexOf("repository.")
            assertTrue(
                "$name reads the library cache without checking whose it is — in the " +
                    "car no login path has run, so this is the only place the check " +
                    "can happen (issue #575)",
                guard >= 0,
            )
            assertTrue(
                "$name checks the owner *after* it has already read the cache; the " +
                    "rows it returns would be the previous account's",
                read < 0 || guard < read,
            )
        }
    }

    @Test
    fun `an unverified owner is refused rather than served`() {
        val body = functionBody(service, "libraryCacheReadable")
        assertTrue(
            "libraryCacheReadable must report Unverified as 'do not read' — failing " +
                "open here renders another account's library (issue #575)",
            body.contains("Unverified"),
        )
        assertTrue(
            "a reconcile that changed the owner must drop the in-memory search " +
                "results too: they were computed while the previous account was " +
                "signed in, and onGetSearchResult answers out of them",
            body.contains("autoSearchResults.clear()"),
        )
    }

    @Test
    fun `the login and launch path reconciles before it adopts legacy rows`() {
        val body = functionBody(scopeProvider, "onAuthenticated")
        val reconcile = body.indexOf("libraryCacheOwner.reconcile()")
        val adopt = body.indexOf("adoptLegacyRowsIfAny")
        assertTrue(
            "UserScopeProvider.onAuthenticated must reconcile the library cache — it " +
                "is the one path every phone sign-in and every app start goes through",
            reconcile >= 0,
        )
        assertTrue("the reconcile must come first", adopt < 0 || reconcile < adopt)
    }

    @Test
    fun `signing out does not erase the recorded owner`() {
        val body = functionBody(tokenManager, "clearTokens")
        assertFalse(
            "the cache's owner has to outlive the session: erasing it on sign-out " +
                "leaves the next sign-in nothing to compare against, which is exactly " +
                "the event-shaped check issue #575 rejected",
            body.contains("library_cache_owner") || body.contains("OWNER"),
        )
    }

    @Test
    fun `nothing clears the library cache on the sign-out path`() {
        // Deliberate, and easy to "fix" back: sign-out keeps the Room cache and
        // the downloaded files so signing back in is cheap (docs/android.md,
        // issue #573), and the state check at the next sign-in is what catches a
        // different account. A clear here would re-download gigabytes for the
        // ordinary same-account sign-out and sign-in.
        val account = source("com/booksync/ui/account/AccountViewModel.kt")
        for (name in listOf("logout", "logoutAll")) {
            val body = functionBody(account, name)
            assertFalse(
                "$name must not clear the library cache — see issue #575",
                body.contains("libraryCacheOwner") || body.contains("clearLibrary"),
            )
        }
    }
}
