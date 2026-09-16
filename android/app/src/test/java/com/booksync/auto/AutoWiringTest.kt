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
            browsePath.contains("emptyMessage") &&
                codeLines(service).any { it.contains("autoMessageItem(AUTO_UNAVAILABLE_MESSAGE)") },
        )
    }

    // --- Signed out: no content by any route (issue #573) ---

    /**
     * Sign-out leaves the Room cache and the downloaded files in place — that is
     * deliberate (`docs/android.md`), it is what makes signing back in cheap.
     * So every Android Auto surface has to ask whether there is an account
     * *before* it reads that cache, and the answer has to be the same one for
     * all of them.
     *
     * The gate must be exactly this: `autoHasAccount(tokenManager.cachedAccessToken())`,
     * read once into a helper the surfaces below call. The car is the only
     * consumer that can be handed another person's library, and the token is the
     * only thing sign-out actually changes.
     */
    @Test
    fun `the service asks one question about the account, and asks it of the token`() {
        assertTrue(
            "AudioPlayerService must define a single signed-in helper over " +
                "autoHasAccount(tokenManager.cachedAccessToken()) — issue #573.",
            codeLines(service).any {
                it.contains("autoHasAccount(tokenManager.cachedAccessToken())")
            },
        )
    }

    /**
     * The regression guard for #573 itself.
     *
     * The bug was not a missing message — the message existed and was pinned by
     * a test. It was that the only way to reach it was `autoEmptyMessage`, wired
     * in as the **empty-list fallback**, so it could only ever appear on a device
     * whose cache happened to be empty. Signed out with a populated cache, the
     * car listed and played the previous account's whole library.
     *
     * So: the service must not name the signed-out message at all. It belongs to
     * the gate in `AutoAccountGate`, which decides on the account and not on the
     * size of a list.
     */
    @Test
    fun `the signed-out message is a gate, not an empty-list fallback`() {
        val offenders = codeLines(service).filter { it.contains("AUTO_SIGNED_OUT_MESSAGE") }
        assertTrue(
            "AudioPlayerService must not reference AUTO_SIGNED_OUT_MESSAGE: " +
                "$offenders. Producing it in the service is how issue #573 " +
                "happened — it was only reachable as the empty-list fallback " +
                "(autoEmptyMessage), so a populated cache hid it forever. The " +
                "gate (autoSignedOutNode / autoGatedBrowse) owns it now.",
            offenders.isEmpty(),
        )
        assertTrue(
            "No helper may choose a message by whether the list came back " +
                "empty: that is the issue #573 shape returning.",
            codeLines(service).none { it.contains("fun autoEmptyMessage(") },
        )
    }

    /**
     * Every browse node, not merely the two tabs: the root as well, and any node
     * id a head unit remembers from a previous session and asks for directly.
     */
    @Test
    fun `browsing is gated on having an account, above every node`() {
        val body = functionBody(service, "onGetChildren")
        assertTrue(
            "onGetChildren must run every node through autoGatedBrowse(...) so " +
                "the root, both tabs and any remembered node id all answer with " +
                "the sign-in leaf when there is no account (issue #573).",
            body.contains("autoGatedBrowse("),
        )
        assertTrue(
            "…and the gate must be the service's own account helper.",
            body.contains("hasAccount()"),
        )
    }

    /**
     * Search is gated separately from browse because it has two halves and a
     * cache of its own between them: `onSearch` runs the query, `onGetSearchResult`
     * hands back what it stored. A result set computed while signed in must not
     * survive a sign-out and be handed to the car afterwards.
     */
    @Test
    fun `voice search returns nothing playable without an account`() {
        assertTrue(
            "onSearch must run through autoGatedSearch(...) — the Auto search " +
                "index is built from the same Room cache as the tree (#573).",
            functionBody(service, "onSearch").contains("autoGatedSearch("),
        )
        assertTrue(
            "onGetSearchResult must check the account too: it answers from " +
                "autoSearchResults, which may hold a result set computed before " +
                "the sign-out (#573).",
            functionBody(service, "onGetSearchResult").contains("hasAccount()"),
        )
    }

    /**
     * The path the issue's downloaded book took. A file already on disk needs no
     * token, so nothing below this gate can refuse it — and `onSetMediaItems` is
     * where *every* play request lands: a tap in the browse tree, a voice query,
     * Assistant's "play X on Tandem", and `MainActivity`'s
     * `MEDIA_PLAY_FROM_SEARCH` bridge.
     */
    @Test
    fun `no play request resolves without an account`() {
        val setItems = functionBody(service, "onSetMediaItems")
        assertTrue(
            "onSetMediaItems must refuse before it resolves anything (#573).",
            setItems.contains("hasAccount()"),
        )
        val gateAt = setItems.lines().indexOfFirst { it.contains("hasAccount()") }
        val firstResolveAt = setItems.lines().indexOfFirst {
            it.contains("resolveMediaItem(") || it.contains("autoSearchBooks(") ||
                it.contains("is CastPlayer")
        }
        assertTrue(
            "The gate must come first: it sits above the Cast branch and above " +
                "every resolve, or a downloaded book still plays (#573).",
            gateAt >= 0 && (firstResolveAt < 0 || gateAt < firstResolveAt),
        )
        assertTrue(
            "onGetItem must refuse to resolve a media id without an account — " +
                "it is how a head unit asks for a row it remembers (#573).",
            functionBody(service, "onGetItem").contains("autoGatedPlayback("),
        )
        assertTrue(
            "onAddMediaItems must be overridden and gated: Media3 routes " +
                "addMediaItem there rather than through onSetMediaItems (#573).",
            codeLines(service).any { it.startsWith("override fun onAddMediaItems(") } &&
                functionBody(service, "onAddMediaItems").contains("hasAccount()"),
        )
        assertTrue(
            "onPlaybackResumption must refuse without an account. Its local " +
                "branch already fails, but the Cast branch dispatches a LOAD " +
                "from a saved media id and would resume the previous account's " +
                "book (#573).",
            functionBody(service, "onPlaybackResumption").contains("hasAccount()"),
        )
    }

    /**
     * Gating the surfaces is not enough on its own: signing out while a book is
     * loaded leaves the session holding it, and the car's own transport controls
     * play whatever the session holds without going through any callback above.
     */
    @Test
    fun `signing out empties the session rather than leaving a book loaded`() {
        assertTrue(
            "The service must watch the token and clear the player when it goes " +
                "away, or the car's play button resumes the previous account's " +
                "book straight out of the session (#573).",
            codeLines(service).any { it.contains("clearMediaItems()") } &&
                codeLines(service).any { it.contains("tokenManager.getAccessToken()") },
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

    // --- Continue Listening lists streamed books too (issue #569) ---

    /**
     * The download predicate that caused issue #569 lived in the DAO, and
     * `RecentlyPlayedQueryTest` is what holds it out of there. This holds it out
     * of the other end: "move the filter to the callers" was one of the fixes
     * the issue offered, and Continue Listening is not a caller that wants it.
     * A book streams since issue #171, so filtering here would hide exactly the
     * book the driver is listening to — the tab's whole purpose.
     */
    @Test
    fun `nothing on the Continue Listening path filters on download state`() {
        val sources = mapOf(
            "AudioPlayerService.buildContinueListeningItems" to
                functionBody(service, "buildContinueListeningItems"),
            // The search index is built from the same two flows, so a filter
            // here would also make a streamed book unsayable to Assistant.
            "AudioPlayerService.loadAutoSearchIndex" to
                functionBody(service, "loadAutoSearchIndex"),
            "AutoBrowseTree.continueListeningBooks" to
                functionBody(source("com/booksync/auto/AutoBrowseTree.kt"), "continueListeningBooks"),
        )
        for ((where, body) in sources) {
            val offenders = codeLines(body).filter {
                it.contains("Downloaded") || it.contains("isDownloaded")
            }
            assertTrue(
                "$where must not filter Continue Listening on download state: " +
                    "$offenders. Since issue #171 an undownloaded book streams; " +
                    "hiding one here is issue #569, where a book played in the " +
                    "car for four minutes was absent at the next connect.",
                offenders.isEmpty(),
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
