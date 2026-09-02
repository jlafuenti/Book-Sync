package com.booksync.player

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Where an audiobook's cover art comes from, in order (issue #331).
 *
 * The bug this pins: the app only ever used art **embedded in the audio file**.
 * A book whose file carries no `covr` atom (M4B) or `APIC` frame (MP3) showed a
 * headphones placeholder in the player, the notification, the lock screen and
 * Android Auto — while Home, Library, Downloaded, Details and the web UI all
 * displayed the cover the server holds for that same book. Re-downloading did
 * not help, because the file was never the missing piece; the app simply never
 * asked the server.
 *
 * Verified on a device by controlled comparison: Ship of Destiny's M4B has a
 * `covr` atom and its cover appeared; The Mad Ship's 992 MB M4B has none and it
 * did not, though the server had
 * `/api/files/covers/Liveship_Traders_2_-_The_Mad_Ship_1737.jpeg` all along.
 *
 * A *plan* rather than a single answer, deliberately: "is there embedded art"
 * cannot be known without doing the extraction, so the caller walks the rungs
 * and stops at the first that yields bytes — the same shape as the reader's
 * restore ladder. Kept as a pure function because `com.booksync.auto.*`, where
 * the file and network work lives, is excluded from Kover; this is the only
 * part that can carry a test.
 */
class CoverArtPlanTest {

    @Test
    fun `a cached cover short-circuits everything`() {
        // The cache is the whole point: neither a 992 MB media scan nor a
        // network round trip should happen once the art is on disk.
        assertEquals(
            listOf(CoverArtRung.Cached),
            coverArtPlan(cachedExists = true, audioFileExists = true, serverCoverPath = "/api/files/covers/x.jpg"),
        )
    }

    @Test
    fun `with no cache, embedded art is tried before the network`() {
        // Local and free; the server rung is the fallback, not the first choice.
        assertEquals(
            listOf(CoverArtRung.Embedded, CoverArtRung.Server("/api/files/covers/x.jpg")),
            coverArtPlan(cachedExists = false, audioFileExists = true, serverCoverPath = "/api/files/covers/x.jpg"),
        )
    }

    @Test
    fun `the server rung exists even when the file has no embedded art`() {
        // The regression itself. Before the fix the plan ended at Embedded, so a
        // book like The Mad Ship had nowhere left to look.
        val plan = coverArtPlan(
            cachedExists = false,
            audioFileExists = true,
            serverCoverPath = "/api/files/covers/Liveship_Traders_2_-_The_Mad_Ship_1737.jpeg",
        )
        assertEquals(
            "a book whose audio file carries no embedded art must still reach the server",
            CoverArtRung.Server("/api/files/covers/Liveship_Traders_2_-_The_Mad_Ship_1737.jpeg"),
            plan.last(),
        )
    }

    @Test
    fun `a book that is not downloaded skips the embedded rung`() {
        // Streaming, or art wanted for a browse item before the file arrives:
        // there is no local file to scan, but the server still has a cover.
        assertEquals(
            listOf(CoverArtRung.Server("/api/files/covers/x.jpg")),
            coverArtPlan(cachedExists = false, audioFileExists = false, serverCoverPath = "/api/files/covers/x.jpg"),
        )
    }

    @Test
    fun `no server cover leaves only the embedded rung`() {
        assertEquals(
            listOf(CoverArtRung.Embedded),
            coverArtPlan(cachedExists = false, audioFileExists = true, serverCoverPath = null),
        )
    }

    @Test
    fun `a blank server path is not a cover`() {
        // The server sends null for "no cover"; empty and whitespace strings have
        // shown up in this codebase before and would build a URL to the API root.
        assertEquals(
            listOf(CoverArtRung.Embedded),
            coverArtPlan(cachedExists = false, audioFileExists = true, serverCoverPath = "   "),
        )
    }

    @Test
    fun `nothing to try is an empty plan, not a crash`() {
        assertEquals(
            emptyList<CoverArtRung>(),
            coverArtPlan(cachedExists = false, audioFileExists = false, serverCoverPath = null),
        )
    }

    // ------------------------------------------------------------------
    // Wiring. Every assertion above passes with a plan nothing walks: a
    // correct, well-tested function and a player still showing headphones.
    // Four guards have shipped inert in this repository already.
    // ------------------------------------------------------------------

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath")
    }

    @Test
    fun `the cover helper walks the plan and can reach the server`() {
        val helper = source("com/booksync/auto/CoverArtHelper.kt")
        assertTrue(
            "CoverArtHelper must use coverArtPlan rather than its own two-rung lookup.",
            helper.contains("coverArtPlan("),
        )
        assertTrue(
            "CoverArtHelper's Server rung must actually fetch — matching only the " +
                "branch label passes while the body returns null, which is exactly " +
                "what a mutation run caught here (issue #331).",
            Regex("""CoverArtRung\.Server\s*->\s*fetchAndCache\(""").containsMatchIn(helper),
        )
        assertTrue(
            "the fetch must issue a request rather than only reading the cache.",
            helper.contains("okHttpClient.newCall("),
        )
    }

    @Test
    fun `every call site supplies the server cover path`() {
        // The parameter defaults to null, so a missed call site silently keeps
        // the old embedded-only behaviour on that surface.
        val service = source("com/booksync/player/AudioPlayerService.kt")
        val calls = Regex("""getCoverUri\(""").findAll(service).count()
        val withPath = Regex("""getCoverUri\([^)]*(?:CoverPath|coverFilename)[^)]*\)""")
            .findAll(service).count()
        assertTrue(
            "all $calls getCoverUri call sites in AudioPlayerService must pass a cover " +
                "path; only $withPath do",
            calls > 0 && calls == withPath,
        )
    }

    @Test
    fun `the player screen falls back to the server cover`() {
        assertTrue(
            "PlayerScreen must render the server cover when there is no embedded art — " +
                "this is the reported symptom (issue #331).",
            source("com/booksync/ui/player/PlayerScreen.kt").contains("coverImageUrl("),
        )
    }

    @Test
    fun `downloading an audiobook warms the cover cache`() {
        // Both audiobook paths, not just one: a mutation that removed only the
        // paired call site left the standalone one behind and satisfied a
        // bare contains() check.
        val worker = source("com/booksync/worker/DownloadWorker.kt")
        // Named per branch rather than counted: a count also matches the
        // `private fun cacheCoverArt(` definition, so dropping one call site
        // still satisfied it — a mutation run caught exactly that.
        assertTrue(
            "the paired audiobook download must pre-cache its cover art",
            Regex("""cacheCoverArt\(\s*pair\.""").containsMatchIn(worker),
        )
        assertTrue(
            "the standalone audiobook download must pre-cache its cover art",
            Regex("""cacheCoverArt\(\s*audiobook\.""").containsMatchIn(worker),
        )
        assertTrue(
            "the pre-cache must invalidate first — a re-download is the one moment " +
                "we know the file may have changed.",
            worker.contains("coverArtHelper.invalidate("),
        )
    }
}
