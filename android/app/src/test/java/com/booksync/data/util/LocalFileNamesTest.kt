package com.booksync.data.util

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Turning a server-supplied filename into a name safe to join onto `filesDir`
 * (issue #177).
 *
 * `ebook.filename` and `audiobook.filename` arrive in the server's JSON and were
 * used verbatim as the on-device path. A server the user typed by mistake, a
 * compromised server once it is internet-facing, or an attacker on a cleartext
 * LAN could answer with `"../datastore/booksync_prefs.preferences_pb"` and have
 * the app overwrite its own token store or Room database with an EPUB body —
 * DataStore then throws `CorruptionException` on the next start, a crash loop
 * until storage is cleared. The `delete*` paths were worse: `.delete()` on the
 * same joined path removes any file under `filesDir`.
 *
 * Writes cannot leave the sandbox on Android 10+, so the blast radius is the
 * app's own data, not the device. It is still the traversal a reviewer files on
 * day one of a public repo.
 *
 * Returns null rather than throwing so callers can choose: a download should
 * fail loudly, a cover lookup should just show no art.
 *
 * Worth knowing: the honest server only ever emits `os.path.basename(...)`, so
 * for every real file this is an identity mapping and no rename migration is
 * needed. Over-rejection, not under-rejection, is the risk here.
 */
class LocalFileNamesTest {

    @Test
    fun `an ordinary filename passes through unchanged`() {
        // The identity-mapping property every already-downloaded file relies on.
        assertEquals("Book.epub", localFileName("Book.epub"))
        assertEquals(
            "Hobb, Robin - [The Liveship Traders 02] - The Mad Ship.epub",
            localFileName("Hobb, Robin - [The Liveship Traders 02] - The Mad Ship.epub"),
        )
        assertEquals(
            "BBC R4 - Bartleby The Scrivener-sp7.mp3",
            localFileName("BBC R4 - Bartleby The Scrivener-sp7.mp3"),
        )
    }

    @Test
    fun `unicode, spaces and punctuation are not the enemy`() {
        // Real libraries are full of these. Rejecting them would break books
        // that work today, which is the likelier failure mode of this change.
        assertEquals("Les Misérables.epub", localFileName("Les Misérables.epub"))
        assertEquals("Book (Unabridged) [2019].m4b", localFileName("Book (Unabridged) [2019].m4b"))
        assertEquals("多和田葉子.epub", localFileName("多和田葉子.epub"))
        assertEquals("a.b.c.epub", localFileName("a.b.c.epub"))
    }

    @Test
    fun `traversal is rejected`() {
        assertNull(localFileName("../evil.bin"))
        assertNull(localFileName("../../datastore/booksync_prefs.preferences_pb"))
        assertNull(localFileName("a/../../b"))
        assertNull(localFileName("dir/nested.epub"))
    }

    @Test
    fun `absolute paths are rejected`() {
        assertNull(localFileName("/abs/path.m4b"))
        assertNull(localFileName("/data/data/com.booksync/databases/booksync.db"))
    }

    @Test
    fun `windows-style separators are rejected too`() {
        // The server is POSIX, but the check is about what the *client* is
        // handed, and a backslash is a separator on the platform this code is
        // written on. Cheap to cover, and it costs no real filename.
        assertNull(localFileName("..\\evil.bin"))
        assertNull(localFileName("dir\\nested.epub"))
    }

    @Test
    fun `the degenerate names are rejected`() {
        assertNull(localFileName(""))
        assertNull(localFileName("   "))
        assertNull(localFileName("."))
        assertNull(localFileName(".."))
    }

    @Test
    fun `a NUL byte is rejected`() {
        // Truncation tricks against native layers below the JVM: the JVM keeps
        // the whole string, a C string stops at the NUL. Written as an escape
        // rather than a literal so the source stays readable.
        assertNull(localFileName("book.epub\u0000.png"))
        assertNull(localFileName("\u0000"))
    }

    @Test
    fun `percent-encoded traversal is kept as a literal name, deliberately`() {
        // "%2e%2e/x" contains a separator, so it is rejected as traversal.
        assertNull(localFileName("%2e%2e/x"))

        // But "%2e%2e" alone has no separator and is NOT decoded by the
        // filesystem: it names a file literally called "%2e%2e". Accepting it
        // is correct — decoding here would invent an attack that does not
        // exist, and would corrupt any real filename containing a percent sign.
        assertEquals("%2e%2e", localFileName("%2e%2e"))
        assertEquals("100%25 Wolf.epub", localFileName("100%25 Wolf.epub"))
    }

    // ------------------------------------------------------------------
    // Wiring. Every assertion above passes with a helper nothing calls:
    // correct, well-tested, and the traversal still wide open. This sweeps
    // the sources for the pattern instead of trusting that I found all 17
    // call sites by hand.
    // ------------------------------------------------------------------

    private fun mainSources(): List<File> {
        var dir = File("").absoluteFile
        repeat(4) {
            val root = File(dir, "app/src/main/java/com/booksync")
            if (root.isDirectory) return root.walkTopDown().filter { it.extension == "kt" }.toList()
            val direct = File(dir, "src/main/java/com/booksync")
            if (direct.isDirectory) return direct.walkTopDown().filter { it.extension == "kt" }.toList()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate the main sources")
    }

    @Test
    fun `nothing joins a server filename onto filesDir directly`() {
        // The shapes that were the bug: File(filesDir, "audiobooks/$name") and
        // File(File(filesDir, "audiobooks"), name). Cover files keyed by the
        // integer audiobook id are fine and deliberately not matched.
        val interpolated = Regex("""filesDir,\s*"(ebooks|audiobooks)/\$""")
        // The outer File( is required: `File(filesDir, "audiobooks")` on its own
        // is just the directory. LocalCastHttpServer takes it that way and does
        // its own `..`/`/` rejection before joining — the one place that already
        // got this right — so matching the bare directory would be a false
        // positive, which is exactly what happened on the first run.
        val nested = Regex("""File\(\s*File\(\s*(?:context\.)?filesDir,\s*"(ebooks|audiobooks)"\s*\)\s*,""")

        val offenders = mainSources().filter { f ->
            val text = f.readText()
            interpolated.containsMatchIn(text) || nested.containsMatchIn(text)
        }.map { it.name }

        assertTrue(
            "these build a path from a server-supplied filename instead of going " +
                "through MediaDownloadRepository.localEbookFile / localAudioFile, so the " +
                "traversal is still open there (issue #177): $offenders",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `the repository sanitises rather than trusting the name`() {
        val repo = mainSources().first { it.name == "MediaDownloadRepository.kt" }.readText()
        assertTrue(
            "MediaDownloadRepository must call localFileName — without it the accessors " +
                "are just a rename of the old join.",
            repo.contains("localFileName("),
        )
        assertTrue(
            "the accessors must assert containment as well as sanitising the name.",
            repo.contains("canonicalPath"),
        )
    }

    @Test
    fun `the cover helper sanitises its own join`() {
        // CoverArtHelper deliberately has no repository dependency, so it is the
        // one place that has to remember on its own.
        val helper = mainSources().first { it.name == "CoverArtHelper.kt" }.readText()
        assertTrue(
            "CoverArtHelper must sanitise the audiobook filename it joins.",
            helper.contains("localFileName("),
        )
    }
}
