package com.booksync.data.local

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Every SQL query that touches per-account data must be scoped (issue #314).
 *
 * This exists because of a specific mistake. The original audit classified
 * *tables* — `bookmarks`/`user_progress`/`bookmark_log`/`acknowledged_items`/
 * `pending_sync` are personal, `book_pairs`/`ebooks`/`audiobooks`/`sync_points`
 * are the shared library — and then scoped every query whose subject was a
 * personal table. Three queries select from a **library** table and reach the
 * personal ones through a JOIN:
 *
 * ```sql
 * SELECT bp.* FROM book_pairs bp
 * INNER JOIN bookmarks b ON bp.id = b.bookPairId   -- unscoped
 * ```
 *
 * Those were filed as "library, no change needed" and shipped. On a real device a
 * second account's Continue Reading was built from the first account's bookmarks
 * and progress. Two rounds of review missed it as well, both having checked
 * "every query on the five tables" — which is the same wrong question.
 *
 * So the rule is enforced on the text of the query rather than on anyone's mental
 * model of which table belongs to whom: if a query mentions a per-account table
 * anywhere — FROM, JOIN or sub-select — it must also mention `scopeKey`.
 */
class ScopedQueryContractTest {

    private val perAccountTables = listOf(
        "bookmarks", "user_progress", "bookmark_log", "acknowledged_items", "pending_sync",
    )

    /**
     * Queries allowed to mention a per-account table without a scope, with the
     * reason. Deliberately tiny: adding an entry should feel like a decision.
     */
    private val exempt = mapOf(
        // Adoption is the one operation that works *across* scopes: it claims rows
        // written before scoping existed. It matches on scopeKey = '' instead.
        "ScopeAdoptionDao" to "claims pre-scoping rows; matches the legacy scope by design",
    )

    private fun daoSources(): List<Pair<String, String>> {
        var dir = File("").absoluteFile
        repeat(4) {
            for (c in listOf(
                File(dir, "app/src/main/java/com/booksync/data/local/dao"),
                File(dir, "src/main/java/com/booksync/data/local/dao"),
            )) if (c.isDirectory) {
                return c.listFiles { f: File -> f.name.endsWith(".kt") }!!
                    .map { it.name to it.readText() }
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("dao sources not found from ${File("").absolutePath}")
    }

    /** Each @Query body paired with the function it annotates. */
    private fun queries(source: String): List<Pair<String, String>> =
        source.split("@Query").drop(1).mapNotNull { chunk ->
            val m = Regex("fun\\s+(\\w+)").find(chunk) ?: return@mapNotNull null
            m.groupValues[1] to chunk.substring(0, m.range.first).replace(Regex("\\s+"), " ")
        }

    @Test
    fun `no query reads per-account data without a scope`() {
        val offenders = mutableListOf<String>()

        for ((fileName, source) in daoSources()) {
            if (exempt.keys.any { fileName.startsWith(it) }) continue
            for ((fn, sql) in queries(source)) {
                val touched = perAccountTables.filter {
                    Regex("\\b$it\\b").containsMatchIn(sql)
                }
                if (touched.isNotEmpty() && !sql.contains("scopeKey")) {
                    offenders += "$fileName::$fn reaches ${touched.joinToString()} " +
                        "without scopeKey"
                }
            }
        }

        assertTrue(
            "These queries read one account's data for whoever is signed in. A JOIN " +
                "counts: selecting from a library table does not make the query " +
                "shared.\n" + offenders.joinToString("\n"),
            offenders.isEmpty(),
        )
    }

    @Test
    fun `the guard actually inspects the DAOs`() {
        // A silently-empty walk would make the test above pass for the wrong reason
        // — the same failure mode as the scan it replaces.
        val all = daoSources().flatMap { (_, src) -> queries(src) }
        assertTrue("found only ${all.size} queries; the DAO scan is broken", all.size > 40)
        assertTrue(
            "no scoped query found at all — the scopeKey check cannot be working",
            all.count { it.second.contains("scopeKey") } > 15,
        )
    }
}
