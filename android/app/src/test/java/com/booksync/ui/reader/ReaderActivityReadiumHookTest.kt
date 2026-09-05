package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins the call shape of the `<head/>` transformer wiring — issue #373.
 *
 * Readium 3.1.2's `PublicationOpener` takes an `onCreatePublication` hook in
 * two places: the constructor and `open(...)`. Only the `open(...)` one is ever
 * invoked — inside `open()` the parameter shadows the constructor property, so
 * a hook passed to the constructor is silently dropped. Wired that way, the
 * container swap never happens and every Calibre `<head/>` book still fails
 * with `net::ERR_FAILED`, which is exactly what the first cut of this fix did
 * on an emulator. This test reads the source rather than instantiating the
 * activity because the failure is a wiring mistake, not a behaviour the pure
 * halves ([normalizeEpubHead], [ResourceFailurePolicy]) can see.
 */
class ReaderActivityReadiumHookTest {

    private fun readerActivitySource(): String {
        var dir = File("").absoluteFile
        repeat(5) {
            val candidate = File(dir, "app/src/main/java/com/booksync/ui/reader/ReaderActivity.kt")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/com/booksync/ui/reader/ReaderActivity.kt")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("ReaderActivity.kt not found from ${File("").absolutePath}")
    }

    /** The `PublicationOpener(...)` constructor call, up to its closing paren. */
    private fun constructorCall(source: String): String {
        // Anchor on the assignment so a KDoc mention of the type is not matched.
        val anchor = source.indexOf("= PublicationOpener(")
        assertTrue("ReaderActivity must construct a PublicationOpener", anchor >= 0)
        val start = anchor + 2
        var depth = 0
        for (i in start until source.length) {
            when (source[i]) {
                '(' -> depth++
                ')' -> { depth--; if (depth == 0) return source.substring(start, i + 1) }
            }
        }
        throw AssertionError("unbalanced PublicationOpener(...) call")
    }

    @Test
    fun `hook is passed to open(), where Readium actually invokes it`() {
        val source = readerActivitySource()
        // Start at the assignment so a KDoc mention of `.open(` is not matched.
        val fromCall = source.substring(source.indexOf("= PublicationOpener("))
        val openCall = Regex("""\.open\s*\((.*?)\)\s*\.getOrNull""", RegexOption.DOT_MATCHES_ALL)
            .find(fromCall)
            ?.groupValues?.get(1)
        assertTrue("expected PublicationOpener(...).open(...).getOrNull()", openCall != null)
        assertTrue(
            "open(...) must receive onCreatePublication = { container = normalizeHeads(container) }",
            Regex("""onCreatePublication\s*=\s*\{\s*container\s*=\s*normalizeHeads\(container\)\s*\}""")
                .containsMatchIn(openCall!!),
        )
    }

    @Test
    fun `hook is not passed to the constructor, which Readium 3_1_2 ignores`() {
        val source = readerActivitySource()
        assertFalse(
            "PublicationOpener(...) constructor must not carry onCreatePublication — " +
                "Readium 3.1.2 never calls it; pass it to open(...) instead",
            constructorCall(source).contains("onCreatePublication"),
        )
    }
}
