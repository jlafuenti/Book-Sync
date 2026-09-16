package com.booksync.player

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The artwork published on the media session (issue #570).
 *
 * The bug: the session's playing item carried
 * `content://<applicationId>.fileprovider/covers/<id>.jpg` as its `artworkUri`.
 * The provider is `exported="false"` — correct, it fronts app-private files —
 * and `grantUriPermissions="true"` only *allows* a grant; something has to make
 * one. The only `grantUriPermission` in the app names Android Auto
 * (`com.google.android.projection.gearhead`) and is called from the browse path
 * alone. SystemUI, which renders the shade's media player, the lock screen and
 * quick settings, is not a session controller and never got a grant, so every
 * read threw and the controls were blank — 102 `SecurityException`s in one
 * session of the DHU pass on issue #172, 36 in another.
 *
 * The fix is not to enumerate consumer packages but to stop publishing a URI
 * nobody outside this process can open: the playing item carries the cover as
 * **bytes**, which Media3's `BitmapLoader.loadBitmapFromMetadata` prefers over
 * the URI and which `LegacyConversions` turns into `METADATA_KEY_ALBUM_ART` on
 * the platform session. Bytes cross a Binder, so they have to be bounded —
 * hence the plan below.
 *
 * Pure and outside `CoverArtHelper` on purpose: that class is Kover-excluded as
 * framework glue (`app/build.gradle.kts`), so logic placed there cannot be
 * covered. Same reason [coverArtPlan] lives here.
 */
class MediaArtworkTest {

    // --- The bounds themselves ---

    @Test
    fun `the plan is bounded well under the Binder transaction limit`() {
        // The metadata is parcelled to SystemUI across a Binder whose whole
        // transaction buffer is ~1 MB, shared with everything else in flight.
        // A book cover straight off disk can be 3000 px and megabytes; the cap
        // is what stops a TransactionTooLargeException taking the session down
        // instead of merely losing the art.
        assertTrue(
            "the byte cap must stay a fraction of the ~1 MB Binder buffer",
            MEDIA_ARTWORK_MAX_BYTES in 32 * 1024..512 * 1024,
        )
        assertTrue(
            "the pixel cap must stay in notification/lock-screen territory — " +
                "SystemUI materialises this as an ARGB_8888 bitmap, so 512 px " +
                "is already 1 MB of heap in its process",
            MEDIA_ARTWORK_MAX_PX in 256..1024,
        )
    }

    @Test
    fun `the encode plan never grows and never exceeds the pixel cap`() {
        val plan = mediaArtworkEncodePlan()
        assertTrue("the plan must have at least one attempt", plan.isNotEmpty())
        assertTrue(
            "no attempt may decode above the pixel cap: $plan",
            plan.all { it.maxPx in 1..MEDIA_ARTWORK_MAX_PX },
        )
        assertTrue(
            "JPEG quality must stay in range: $plan",
            plan.all { it.quality in 1..100 },
        )
        // Cheapest-to-fall-back ordering: each attempt is no more expensive in
        // bytes than the one before it, so walking the plan converges.
        plan.zipWithNext().forEach { (a, b) ->
            assertTrue(
                "attempt $b must not be larger than $a — the plan is a descent",
                b.maxPx <= a.maxPx && (b.maxPx < a.maxPx || b.quality <= a.quality),
            )
        }
    }

    @Test
    fun `fits rejects empty and oversized encodings`() {
        assertFalse("no bytes is not artwork", mediaArtworkFits(0))
        assertTrue(mediaArtworkFits(1))
        assertTrue(mediaArtworkFits(MEDIA_ARTWORK_MAX_BYTES))
        assertFalse(mediaArtworkFits(MEDIA_ARTWORK_MAX_BYTES + 1))
    }

    // --- Walking the plan ---

    @Test
    fun `the first encoding that fits is the one published`() {
        val tried = mutableListOf<ArtworkEncode>()
        val bytes = encodeMediaArtwork(maxBytes = 100) { attempt ->
            tried += attempt
            ByteArray(50)
        }
        assertNotNull(bytes)
        assertEquals("a fitting first attempt must end the walk", 1, tried.size)
        assertEquals(mediaArtworkEncodePlan().first(), tried.single())
    }

    @Test
    fun `an oversized encoding steps down the plan`() {
        // The real shape of the problem: a 3000 px cover re-encoded at the top
        // quality can still be over the cap, and the answer is a smaller
        // encode, not a URI SystemUI cannot open.
        val sizes = mutableListOf<Int>()
        val bytes = encodeMediaArtwork(
            plan = listOf(ArtworkEncode(512, 85), ArtworkEncode(512, 65), ArtworkEncode(256, 65)),
            maxBytes = 100,
        ) { attempt ->
            val size = if (attempt.quality == 85) 500 else if (attempt.maxPx == 512) 200 else 80
            sizes += size
            ByteArray(size)
        }
        assertEquals("the walk must try every attempt until one fits", listOf(500, 200, 80), sizes)
        assertEquals(80, bytes?.size)
    }

    @Test
    fun `a cover that never fits publishes no artwork at all`() {
        // Deliberately null rather than "fall back to the URI". An unreadable
        // FileProvider URI is the bug; re-introducing it as a fallback would
        // bring back the SecurityException spam for exactly the covers most
        // likely to be interesting.
        assertNull(encodeMediaArtwork(maxBytes = 10) { ByteArray(5000) })
    }

    @Test
    fun `an encoder that fails on one attempt still tries the next`() {
        // BitmapFactory returns null for a truncated or non-image file, and the
        // covers cache is keyed on existence alone.
        val bytes = encodeMediaArtwork(
            plan = listOf(ArtworkEncode(512, 85), ArtworkEncode(256, 65)),
            maxBytes = 100,
        ) { attempt -> if (attempt.maxPx == 512) null else ByteArray(20) }
        assertEquals(20, bytes?.size)
    }

    @Test
    fun `an encoder that always fails yields nothing rather than throwing`() {
        assertNull(encodeMediaArtwork { null })
    }

    @Test
    fun `an empty encoding is not published`() {
        // A zero-length array would satisfy a naive null check and then hand
        // BitmapLoader.decodeBitmap nothing to decode.
        assertNull(encodeMediaArtwork { ByteArray(0) })
    }

    // ------------------------------------------------------------------
    // Wiring. Every assertion above passes with a plan nothing walks —
    // AudioPlayerService, CoverArtHelper and PlayerScreen are all excluded
    // from Kover, so the only thing that can hold them is reading the source.
    // ------------------------------------------------------------------

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

    private val service by lazy { source("com/booksync/player/AudioPlayerService.kt") }
    private val browseTree by lazy { source("com/booksync/auto/AutoBrowseTree.kt") }
    private val coverHelper by lazy { source("com/booksync/auto/CoverArtHelper.kt") }
    private val playerScreen by lazy { source("com/booksync/ui/player/PlayerScreen.kt") }

    @Test
    fun `the playing item is built from artwork bytes, not a FileProvider URI`() {
        // The regression this exists to catch: someone swaps the bytes back for
        // the URI because it is one line shorter, and the phone's media
        // controls go blank again with no test failing (issue #570).
        for (name in listOf("buildPairMediaItem", "buildAudiobookMediaItem")) {
            val body = functionBody(service, name)
            assertTrue(
                "$name must pass artworkData to autoBookItem — the item it " +
                    "builds is what the media session publishes, and SystemUI " +
                    "has no grant for a content:// cover URI (issue #570).",
                body.contains("artworkData"),
            )
            assertFalse(
                "$name must not pass a cover Uri: SystemUI reads the session's " +
                    "ART_URI/DISPLAY_ICON_URI itself, in its own process.",
                body.contains("coverUri"),
            )
        }
        assertTrue(
            "resolveMediaItem must resolve artwork as bytes.",
            functionBody(service, "resolveMediaItem").contains("getCoverArtworkData("),
        )
    }

    @Test
    fun `an item never carries both a cover URI and cover bytes`() {
        // Media3 mirrors artworkUri into the platform session metadata as
        // ART_URI and DISPLAY_ICON_URI whether or not bytes are also present,
        // and SystemUI tries the URI first. Publishing both keeps the
        // SecurityException — and the blank controls on any consumer that
        // stops at the failed URI.
        val body = functionBody(browseTree, "autoBookItem")
        assertTrue(
            "autoBookItem must set artwork bytes when it has them (issue #570).",
            body.contains("setArtworkData("),
        )
        assertTrue(
            "…and must choose between bytes and a URI rather than setting both.",
            Regex("""if\s*\(\s*artworkData\s*!=\s*null\s*\)""").containsMatchIn(body),
        )
        assertTrue(
            "setArtworkUri must sit in the else branch, after setArtworkData.",
            body.indexOf("setArtworkData(") < body.indexOf("setArtworkUri("),
        )
    }

    @Test
    fun `the phone's own artwork warm-up publishes bytes too`() {
        // PlayerScreen re-publishes the metadata of the item already playing
        // when it resolves a cover late (issue #331). It used to set
        // artworkUri, which put the ungranted URI straight back on the session
        // even after the service stopped doing so.
        assertTrue(
            "PlayerScreen must warm the notification with artwork bytes.",
            codeLines(playerScreen).any { it.contains("setArtworkData(") },
        )
        assertTrue(
            "PlayerScreen must not put a FileProvider URI on the session " +
                "(issue #570).",
            codeLines(playerScreen).none { it.contains("setArtworkUri(") },
        )
    }

    @Test
    fun `the cover helper bounds the bytes it hands the session`() {
        assertTrue(
            "CoverArtHelper must produce session artwork through the bounded " +
                "walk — an unbounded cover on the session risks " +
                "TransactionTooLargeException, not merely a large parcel. " +
                "(Trailing-lambda syntax counts, so match the name, not a paren.)",
            codeLines(coverHelper).any { it.contains("encodeMediaArtwork") },
        )
        assertTrue(
            "…and must downsample rather than decode the cover at full size.",
            coverHelper.contains("coverArtInSampleSize("),
        )
    }

    @Test
    fun `the FileProvider stays unexported`() {
        // The one fix that must never be taken: exporting the provider would
        // hand every app on the device the app-private files it fronts.
        val manifest = repoFile("src/main/AndroidManifest.xml").readText()
        val provider = manifest.substringAfter("androidx.core.content.FileProvider")
            .substringBefore("</provider>")
        assertTrue(
            "the cover FileProvider must stay android:exported=\"false\" " +
                "(issue #570).",
            provider.contains("android:exported=\"false\""),
        )
    }

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
}
