package com.booksync.data.local

import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Make database corruption visible before the platform deletes the evidence
 * (issue #364).
 *
 * When SQLite reports a corrupt file, the default handler's answer is to delete
 * it. Room then opens a brand-new empty database and the app carries on as if
 * nothing happened: every bookmark and position with `syncedToServer = false`,
 * the whole `pending_sync` offline queue and the acknowledged-items table are
 * gone, with nothing on screen and nothing in the diagnostics log to say so.
 * That is the same silent-wipe shape #168 removed `fallbackToDestructiveMigration`
 * to stop.
 *
 * #364 also removed `domain="database"` from the backup rules, which closes the
 * path that produced this in practice — a WAL database restored without its
 * sidecar. This is the belt to that braces: corruption has other causes (a
 * killed write, a failing flash chip), and when one of them fires the log line
 * is the only thing that will ever explain where the data went.
 *
 * The deletion still happens. Refusing it would leave the app unable to open its
 * own database, which is worse; the goal is a record, not a rescue.
 */

private const val CORRUPTION_TAG = "DatabaseCorruption"

/**
 * The line appended to the diagnostics log. Pure and separately testable — it is
 * written from inside a corruption callback, which is not a place anyone gets to
 * debug twice.
 *
 * [path] is whatever `SupportSQLiteDatabase.getPath()` reports, which is null for
 * an in-memory database and can be null once the file is already gone.
 */
fun formatDatabaseCorruptionLine(path: String?, timestampMs: Long): String {
    val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date(timestampMs))
    val where = path ?: "(unknown path)"
    return "E/$CORRUPTION_TAG: $stamp SQLite reported corruption in $where — " +
        "Android is deleting it. Unsynced positions, the offline queue and the " +
        "acknowledged-items table are lost; everything already synced returns on " +
        "the next sign-in."
}

/**
 * A [SupportSQLiteOpenHelper.Callback] that records corruption and then lets the
 * real one get on with it.
 *
 * Room has no `setDatabaseErrorHandler`, but `FrameworkSQLiteOpenHelper` installs
 * a `DatabaseErrorHandler` that forwards to this callback's [onCorruption] — so
 * wrapping the callback is the supported way in. Every other method is a
 * pass-through: Room's own callback owns migrations and identity checks, and this
 * must not become a second opinion on any of them.
 */
class DiagnosticDatabaseErrorHandler(
    private val delegate: SupportSQLiteOpenHelper.Callback,
    private val onCorruption: (String) -> Unit,
    private val clock: () -> Long = System::currentTimeMillis,
) : SupportSQLiteOpenHelper.Callback(delegate.version) {

    override fun onConfigure(db: SupportSQLiteDatabase) = delegate.onConfigure(db)

    override fun onCreate(db: SupportSQLiteDatabase) = delegate.onCreate(db)

    override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) =
        delegate.onUpgrade(db, oldVersion, newVersion)

    override fun onDowngrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) =
        delegate.onDowngrade(db, oldVersion, newVersion)

    override fun onOpen(db: SupportSQLiteDatabase) = delegate.onOpen(db)

    override fun onCorruption(db: SupportSQLiteDatabase) {
        // Log first: the delegate is what deletes the file, and `db.path` may
        // stop answering once it has. Wrapped because a logging failure must
        // never be what stops the database from recovering.
        runCatching { onCorruption(formatDatabaseCorruptionLine(db.path, clock())) }
        delegate.onCorruption(db)
    }
}

/**
 * The factory to hand to `RoomDatabase.Builder.openHelperFactory`. Wraps Room's
 * callback in [DiagnosticDatabaseErrorHandler] and otherwise builds exactly the
 * helper Room would have built for itself.
 */
fun corruptionLoggingOpenHelperFactory(
    onCorruption: (String) -> Unit,
    delegateFactory: SupportSQLiteOpenHelper.Factory = FrameworkSQLiteOpenHelperFactory(),
): SupportSQLiteOpenHelper.Factory = SupportSQLiteOpenHelper.Factory { configuration ->
    delegateFactory.create(
        SupportSQLiteOpenHelper.Configuration.builder(configuration.context)
            .name(configuration.name)
            .callback(DiagnosticDatabaseErrorHandler(configuration.callback, onCorruption))
            .noBackupDirectory(configuration.useNoBackupDirectory)
            .allowDataLossOnRecovery(configuration.allowDataLossOnRecovery)
            .build(),
    )
}
