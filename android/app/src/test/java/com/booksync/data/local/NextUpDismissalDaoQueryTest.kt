package com.booksync.data.local

import java.io.File
import java.sql.Connection
import java.sql.DriverManager
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The SQL behind `NextUpDismissalDao` (issue #923), run for real. There is no
 * `androidTest` source set, so Room cannot run; this reads the `@Query`
 * statements out of the DAO source and executes them against an in-memory
 * SQLite built from the committed schema export, the way
 * [LibraryCacheClearQueryTest] does.
 */
class NextUpDismissalDaoQueryTest {

    private lateinit var conn: Connection

    @Before
    fun setUp() {
        conn = DriverManager.getConnection("jdbc:sqlite::memory:")
        exec(createSql())
    }

    @After
    fun tearDown() = conn.close()

    private fun repoFile(relativePath: String): File {
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(File(dir, "app/$relativePath"), File(dir, relativePath))) {
                if (candidate.exists()) return candidate
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath")
    }

    private fun createSql(): String {
        val entities = Json.parseToJsonElement(
            repoFile("schemas/com.booksync.data.local.BookSyncDatabase/26.json").readText()
        ).jsonObject["database"]!!.jsonObject["entities"]!!.jsonArray
        val e = entities.map { it.jsonObject }
            .first { it["tableName"]!!.jsonPrimitive.content == "next_up_dismissals" }
        return e["createSql"]!!.jsonPrimitive.content.replace("\${TABLE_NAME}", "next_up_dismissals")
    }

    private val dao by lazy {
        repoFile("src/main/java/com/booksync/data/local/dao/NextUpDismissalDao.kt").readText()
    }

    /** The `@Query` text of the DAO function named [fn]. */
    private fun query(fn: String): String {
        val chunk = dao.split("@Query").drop(1).first { Regex("fun\\s+$fn\\b").containsMatchIn(it) }
        return Regex("\"([^\"]*)\"").find(chunk)!!.groupValues[1]
    }

    private fun exec(sql: String) = conn.createStatement().use { it.execute(sql) }

    private fun keys(): List<String> =
        conn.createStatement().executeQuery("SELECT seriesKey FROM next_up_dismissals ORDER BY seriesKey").use { rs ->
            buildList { while (rs.next()) add(rs.getString(1)) }
        }

    @Test
    fun `deleteByKey removes only the named series`() {
        exec("INSERT INTO next_up_dismissals VALUES ('axis saga', 'Axis Saga', 1)")
        exec("INSERT INTO next_up_dismissals VALUES ('other', 'Other', 2)")

        conn.prepareStatement(query("deleteByKey").replace(":key", "?")).use {
            it.setString(1, "axis saga")
            it.executeUpdate()
        }

        assertEquals(listOf("other"), keys())
    }

    @Test
    fun `clear empties the table`() {
        exec("INSERT INTO next_up_dismissals VALUES ('axis saga', 'Axis Saga', 1)")
        exec(query("clear"))
        assertTrue(keys().isEmpty())
    }

    @Test
    fun `observeAll selects every row`() {
        exec("INSERT INTO next_up_dismissals VALUES ('axis saga', 'Axis Saga', 1)")
        exec("INSERT INTO next_up_dismissals VALUES ('other', 'Other', 2)")
        val n = conn.createStatement().executeQuery(query("observeAll")).use {
            var c = 0; while (it.next()) c++; c
        }
        assertEquals(2, n)
    }
}
