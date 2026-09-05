package com.booksync.data.local

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Changing the server must never wipe the local database (issues #147, #314).
 *
 * The demo sign-in stores the server URL in the middle of establishing a session,
 * which raised a reasonable worry: if anything in the app resets the cache when
 * the server changes, that reset would be racing `UserScopeProvider.onAuthenticated`
 * as it opens Room. Nothing does — and this pins that, because "nothing does" is
 * an invariant somebody could break in one line while adding a perfectly sensible
 * "clear the old server's books" feature.
 *
 * It is also the *right* invariant rather than an accident of the current code.
 * Issue #314 replaced exactly that primitive: clearing the previous user's rows
 * destroyed `syncedToServer = 0` reading positions the server had never seen, and
 * only ever covered the tables somebody remembered to name. Scoping every query by
 * `(server, user)` replaced it, so the wrong account cannot *see* rows that are
 * not its own and nothing has to be deleted to keep them apart.
 *
 * Source-text, in the `BuildConfigPinsTest` / `DiGraphWiringTest` style: no JVM
 * test can observe Room being deleted out from under another thread, and the
 * shape is one anybody can read.
 */
class DatabaseResetGuardTest {

    private fun mainSources(): List<File> {
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(
                File(dir, "app/src/main/java/com/booksync"),
                File(dir, "src/main/java/com/booksync"),
            )) {
                if (candidate.isDirectory) {
                    return candidate.walkTopDown().filter { it.name.endsWith(".kt") }.toList()
                }
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate com/booksync sources from ${File("").absolutePath}")
    }

    /** Lines matching [pattern], comments excluded, as "file:line". */
    private fun hits(pattern: Regex): List<String> {
        val blockComment = Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL)
        return mainSources().flatMap { file ->
            file.readText()
                .replace(blockComment) { m -> m.value.replace(Regex("[^\n]"), " ") }
                .lineSequence()
                .withIndex()
                .filterNot { (_, line) -> line.trimStart().startsWith("//") }
                .filter { (_, line) -> pattern.containsMatchIn(line) }
                .map { (i, line) -> "${file.name}:${i + 1}: ${line.trim()}" }
                .toList()
        }
    }

    @Test
    fun `the guard is actually reading the sources`() {
        // A silently-empty walk would make every assertion below pass for the
        // wrong reason.
        val files = mainSources()
        assertTrue("found only ${files.size} Kotlin sources", files.size > 50)
        assertTrue(
            "expected to find the Room database class",
            files.any { it.name == "BookSyncDatabase.kt" },
        )
    }

    @Test
    fun `nothing deletes or truncates the local database`() {
        val offenders = hits(Regex("""\b(deleteDatabase|clearAllTables)\s*\("""))

        assertTrue(
            "The local database is wiped somewhere: $offenders. Rows are " +
                "partitioned by (server, user) precisely so that switching " +
                "server or account never has to delete anything — see " +
                "UserScopeProvider and issue #314. A reset here would also race " +
                "the sign-in that triggers it: the demo flow stores the server " +
                "URL a few milliseconds before onAuthenticated opens Room.",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `changing the server url does not cascade into anything but the UI`() {
        // Every collector of serverUrlFlow, so a future one that resets caches has
        // to be added here deliberately rather than noticed after a corrupted
        // database on somebody's phone. Today: two ViewModels that display the
        // URL, and the login screen.
        val collectors = hits(Regex("""\bserverUrlManager\.serverUrlFlow\b"""))
            .map { it.substringBefore(':') }
            .distinct()
            .sorted()

        assertEquals(
            "Something new reads serverUrlFlow: $collectors. If it resets or " +
                "reloads local data on a server change, it will run while the " +
                "demo sign-in is opening Room — read DemoSignIn before adding it.",
            listOf("AccountViewModel.kt", "HomeViewModel.kt", "LoginScreen.kt"),
            collectors,
        )
    }

    @Test
    fun `the detector would catch a reset if one were added`() {
        // Walking a clean tree only says the tree is clean; it cannot say the
        // check would notice otherwise.
        val pattern = Regex("""\b(deleteDatabase|clearAllTables)\s*\(""")
        assertTrue(pattern.containsMatchIn("context.deleteDatabase(\"booksync.db\")"))
        assertTrue(pattern.containsMatchIn("    db.clearAllTables()"))
        assertTrue(pattern.containsMatchIn("").not())
    }
}
