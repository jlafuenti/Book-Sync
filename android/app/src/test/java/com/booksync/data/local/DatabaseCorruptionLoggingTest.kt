package com.booksync.data.local

import io.mockk.every
import io.mockk.mockk
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #364. The line this formatter produces is the only trace a destructive
 * corruption recovery will ever leave — the platform deletes the file, Room
 * opens an empty one, and the app carries on with no error anywhere. It is
 * written from inside a callback that fires roughly never, so it is pinned here
 * rather than discovered in the field.
 */
class DatabaseCorruptionLoggingTest {

    private val fixedClock = { 1_756_857_600_000L }

    @Test
    fun `the line names the database that is about to be deleted`() {
        val line = formatDatabaseCorruptionLine("/data/user/0/com.booksync/databases/booksync.db", 0L)

        assertTrue(line, line.contains("/data/user/0/com.booksync/databases/booksync.db"))
    }

    @Test
    fun `the line says the data is being lost, not merely that something failed`() {
        // "corruption detected" on its own reads as a warning about a file. The
        // point of the line is that unsynced work is going away.
        val line = formatDatabaseCorruptionLine("/db/booksync.db", 0L)

        assertTrue(line, line.contains("deleting"))
        assertTrue(line, line.contains("offline queue"))
        assertTrue(line, line.contains("Unsynced positions"))
    }

    @Test
    fun `a null path does not produce the word null`() {
        // getPath() is null for an in-memory database and can be null once the
        // file is already gone — which is exactly when this runs.
        val line = formatDatabaseCorruptionLine(null, 0L)

        assertTrue(line, line.contains("(unknown path)"))
        assertTrue(line, !line.contains("null"))
    }

    @Test
    fun `the line carries a timestamp, because the log has no other clock for it`() {
        val line = formatDatabaseCorruptionLine("/db/booksync.db", fixedClock())

        assertTrue(line, Regex("""\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}""").containsMatchIn(line))
    }

    // --- the callback wrapper ---------------------------------------------

    /** Records what Room's own callback was asked to do. */
    private class RecordingCallback : SupportSQLiteOpenHelper.Callback(20) {
        val calls = mutableListOf<String>()
        override fun onCreate(db: SupportSQLiteDatabase) { calls += "onCreate" }
        override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) {
            calls += "onUpgrade:$oldVersion->$newVersion"
        }
        override fun onOpen(db: SupportSQLiteDatabase) { calls += "onOpen" }
        override fun onCorruption(db: SupportSQLiteDatabase) { calls += "onCorruption" }
    }

    private fun fakeDb(path: String?): SupportSQLiteDatabase =
        mockk<SupportSQLiteDatabase>().also { every { it.path } returns path }

    @Test
    fun `corruption is logged and then handled exactly as before`() {
        val delegate = RecordingCallback()
        val logged = mutableListOf<String>()
        val handler = DiagnosticDatabaseErrorHandler(delegate, logged::add, fixedClock)

        handler.onCorruption(fakeDb("/db/booksync.db"))

        assertEquals(1, logged.size)
        assertTrue(logged[0], logged[0].contains("/db/booksync.db"))
        // Still delegated: refusing the deletion would leave the app unable to
        // open its own database, which is worse than losing the cache.
        assertEquals(listOf("onCorruption"), delegate.calls)
    }

    @Test
    fun `a failing log sink does not stop the recovery`() {
        val delegate = RecordingCallback()
        val handler = DiagnosticDatabaseErrorHandler(
            delegate,
            { throw IllegalStateException("disk full") },
            fixedClock,
        )

        handler.onCorruption(fakeDb("/db/booksync.db"))

        assertEquals(listOf("onCorruption"), delegate.calls)
    }

    @Test
    fun `the version comes from Room's callback, not from a constant`() {
        // The version is what SQLiteOpenHelper compares against the file to
        // decide whether to migrate. Hard-coding one here would silently break
        // the next schema bump.
        val delegate = RecordingCallback()

        val handler = DiagnosticDatabaseErrorHandler(delegate, {}, fixedClock)

        assertEquals(delegate.version, handler.version)
    }

    @Test
    fun `every other callback is a pass-through`() {
        val delegate = RecordingCallback()
        val handler = DiagnosticDatabaseErrorHandler(delegate, {}, fixedClock)
        val db = fakeDb("/db/booksync.db")

        handler.onCreate(db)
        handler.onUpgrade(db, 19, 20)
        handler.onOpen(db)

        // Room's callback owns migrations and the identity hash check; this
        // wrapper must never become a second opinion on any of them.
        assertEquals(listOf("onCreate", "onUpgrade:19->20", "onOpen"), delegate.calls)
    }
}
