package com.booksync.auto

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The car app quality checklist items that cannot be reached from a JVM test
 * (issue #172).
 *
 * `AudioPlayerService` needs a `MediaLibrarySession` and a car head unit, and is
 * excluded from Kover for exactly that reason — so the checklist rules it has to
 * keep are pinned here by reading the source, in the style of
 * `MediaSourceWiringTest` and `MediaIdWiringTest`. Each assertion below is one
 * line of the checklist Jesse walks on the Desktop Head Unit (see
 * `docs/android.md`); the test is what stops a later edit from quietly undoing
 * it between submissions.
 */
class AutoWiringTest {

    private fun repoFile(relativePath: String): File {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/$relativePath")
            if (candidate.exists()) return candidate
            val direct = File(dir, relativePath)
            if (direct.exists()) return direct
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    private fun source(relativePath: String) = repoFile("src/main/java/$relativePath").readText()

    /** Comments don't count — a call site that became a comment is the bug. */
    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    private fun functionBody(text: String, name: String): String {
        val lines = text.lines()
        val start = lines.indexOfFirst { it.contains("fun $name(") }
        if (start < 0) throw AssertionError("No function named $name")
        val end = lines.drop(start + 1).indexOfFirst {
            Regex("""^\s{0,8}(private |internal |public |override )*(suspend )?fun \w""")
                .containsMatchIn(it)
        }
        val last = if (end < 0) lines.size else start + 1 + end
        return lines.subList(start, last).joinToString("\n")
    }

    private val service by lazy { source("com/booksync/player/AudioPlayerService.kt") }
    private val mainActivity by lazy { source("com/booksync/MainActivity.kt") }
    private val manifest by lazy { repoFile("src/main/AndroidManifest.xml").readText() }

    /**
     * Everything between the "Browse tree helpers" banner and the resume helper
     * that follows it: every function a browse request runs through.
     */
    private val browsePath: String by lazy {
        val from = service.indexOf("// Browse tree helpers")
        val to = service.indexOf("private suspend fun refreshPositionBeforeResume")
        assertTrue("Browse-tree helper block not found", from in 0 until to)
        service.substring(from, to)
    }

    // --- Voice search ---

    @Test
    fun `the session implements both halves of Media3 search`() {
        // Without both, Media3 answers every Assistant search with
        // RESULT_ERROR_NOT_SUPPORTED — which is what the app did for a year
        // while the manifest advertised voice search.
        for (callback in listOf("onSearch(", "onGetSearchResult(")) {
            assertTrue(
                "AudioPlayerService must override $callback (issue #172).",
                codeLines(service).any { it.startsWith("override fun $callback") },
            )
        }
    }

    @Test
    fun `search ranking is decided by the pure matcher, not in the service`() {
        assertTrue(
            "AudioPlayerService must rank through autoSearch(...) — the service " +
                "is excluded from Kover, so matching logic written here is untested.",
            codeLines(service).any { it.contains("autoSearch(") },
        )
        assertTrue(
            "The service must not hand-roll title matching.",
            codeLines(service).none {
                it.contains("title.contains(") || it.contains("title.startsWith(")
            },
        )
    }

    @Test
    fun `a spoken play request is resolved to a real media id before playback`() {
        val body = functionBody(service, "onSetMediaItems")
        assertTrue(
            "onSetMediaItems must recognise a MediaItem that carries only a " +
                "searchQuery — that is how Assistant, Auto's legacy " +
                "onPlayFromSearch bridge and MainActivity all arrive.",
            body.contains("requestMetadata") && body.contains("searchQuery"),
        )
        assertTrue(
            "…and resolve it to a pair_/audiobook_ item the rest of the " +
                "service understands.",
            body.contains("autoSearchBooks(") && body.contains("resolveMediaItem("),
        )
    }

    @Test
    fun `the phone still answers MEDIA_PLAY_FROM_SEARCH it advertises`() {
        assertTrue(
            "The manifest still declares the MEDIA_PLAY_FROM_SEARCH filter.",
            manifest.contains("android.media.action.MEDIA_PLAY_FROM_SEARCH"),
        )
        assertTrue(
            "MainActivity must handle it. An advertised voice action that opens " +
                "the app on Home and does nothing is a checklist failure " +
                "(issue #172); if the filter is ever dropped, drop this too.",
            codeLines(mainActivity).any { it.contains("INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH") },
        )
        assertTrue(
            "…and must forward the query to the session rather than matching " +
                "books itself.",
            codeLines(mainActivity).any { it.contains("setSearchQuery(") },
        )
    }

    // --- No auto-play on connect ---

    @Test
    fun `connecting to a car does not start playback by itself`() {
        val body = functionBody(service, "onPlaybackResumption")
        assertTrue(
            "The local-playback branch of onPlaybackResumption must keep " +
                "returning a failed future. Media3 reads a successful one as " +
                "'here is what to resume' and the car starts playing the moment " +
                "the phone connects — a car app quality failure, and the reason " +
                "the stale-SharedPrefs position bug was even visible.",
            body.contains("immediateFailedFuture"),
        )
        assertTrue(
            "Nothing in the resumption path may call play() for local playback.",
            codeLines(body).none { it.contains(".play()") },
        )
    }

    @Test
    fun `browsing the tree never starts playback`() {
        for (name in listOf("onGetLibraryRoot", "onGetChildren", "onGetItem", "onSearch")) {
            assertTrue(
                "$name must not start playback — browsing is not a play command.",
                codeLines(functionBody(service, name)).none {
                    it.contains(".play()") || it.contains("playWhenReady = true")
                },
            )
        }
    }

    // --- Browse content loads in time ---

    @Test
    fun `the browse path reads Room and never waits on the server`() {
        val offenders = codeLines(browsePath).filter {
            it.contains("repository.refresh") ||
                it.contains("refreshPositionBeforeResume(") ||
                it.contains("ensureSyncMapCached(")
        }
        assertTrue(
            "The browse tree must be built from Room alone: $offenders. One " +
                "server round trip per row is how browse content misses the " +
                "car checklist's load-time budget, and the position shown in " +
                "Continue Listening is already the DB's.",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `cover art cannot hold a browse node open`() {
        assertTrue(
            "Cover art has a network rung (issue #331), so the browse path must " +
                "bound it — otherwise an unreachable server holds the whole list " +
                "behind one OkHttp timeout per book.",
            browsePath.contains("withTimeoutOrNull(AUTO_COVER_ART_BUDGET_MS)"),
        )
        val budget = Regex("""AUTO_COVER_ART_BUDGET_MS = ([\d_]+)L""")
            .find(service)?.groupValues?.get(1)?.replace("_", "")?.toLong()
            ?: throw AssertionError("AUTO_COVER_ART_BUDGET_MS not declared")
        assertTrue(
            "The budget ($budget ms) must leave room inside the ~10 s the car " +
                "checklist allows for browse content.",
            budget in 1_000..8_000,
        )
    }

    // --- Error state ---

    @Test
    fun `an empty or unreachable library shows a message, never a blank list`() {
        assertTrue(
            "Every browse node must fall back to a message leaf.",
            browsePath.contains("autoEmptyMessage(") &&
                codeLines(service).any { it.contains("autoMessageItem(AUTO_UNAVAILABLE_MESSAGE)") },
        )
        assertTrue(
            "Signed out is its own message — nothing in the car can fix it.",
            browsePath.contains("AUTO_SIGNED_OUT_MESSAGE"),
        )
    }

    // --- The tree's shape lives where it can be tested ---

    @Test
    fun `the service delegates the tree's shape to AutoBrowseTree`() {
        for ((name, call) in listOf(
            "buildRootTabs" to "autoRootTabs(",
            "buildContinueListeningItems" to "continueListeningBooks(",
            "buildLibraryItems" to "libraryBooks(",
        )) {
            assertTrue(
                "AudioPlayerService.$name must delegate to com.booksync.auto — " +
                    "tab order, node caps and ordering decided in the service " +
                    "are decided where Kover cannot see them (issue #172).",
                functionBody(service, name).contains(call),
            )
        }
    }

    @Test
    fun `the auto package is covered rather than excluded`() {
        val gradle = repoFile("build.gradle.kts").readText()
        assertTrue(
            "com.booksync.auto.* must not be blanket-excluded from Kover any " +
                "more — the browse tree and the search matcher live there and " +
                "are tested. Only CoverArtHelper, which needs a real " +
                "FileProvider and MediaMetadataRetriever, stays excluded.",
            codeLines(gradle).none { it.contains("\"com.booksync.auto.*\"") },
        )
    }
}
