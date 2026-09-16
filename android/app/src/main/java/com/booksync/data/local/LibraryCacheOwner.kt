package com.booksync.data.local

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import com.booksync.data.local.dao.LibraryCacheDao
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

private const val TAG = "LibraryCacheOwner"

private val KEY_OWNER_SERVER = stringPreferencesKey("library_cache_owner_server")
private val KEY_OWNER_USER = intPreferencesKey("library_cache_owner_user")

/** Directories under `filesDir` the library cache owns. */
private const val EBOOKS_DIR = "ebooks"
private const val AUDIOBOOKS_DIR = "audiobooks"
private const val COVERS_DIR = "covers"

/** What [LibraryCacheOwner.reconcile] found, and what the caller has to do about it. */
enum class LibraryCacheReconcile {
    /** The cache belongs to the account that is signed in. Read it. */
    Unchanged,

    /**
     * Same server, a different user. The rows and the downloads stand — one
     * Tandem's library is shared by every account on it — but anything held in
     * memory for the previous account has to go.
     */
    AccountChanged,

    /** A different server. The cache described other books entirely; it is gone. */
    ServerChanged,

    /**
     * The owner could not be established, or clearing it failed. **Not
     * permission to read**: callers refuse rather than risk rendering another
     * account's library.
     */
    Unverified,
}

/**
 * Which account the local library cache belongs to (issue #575).
 *
 * Issue #314 partitioned the reading tables by `(server, user)` so the wrong
 * account cannot see another's positions. The library tables were left global
 * and nothing refreshed or cleared them at login, so from the moment a second
 * account signed in until it opened the Library screen against a reachable
 * server, the phone and the car listed the previous account's books, the voice
 * index contained them, and a **downloaded** one played straight off the disk
 * with no token involved. Offline that window never closed.
 *
 * **A check on state, not a hook on an event.** Clearing on sign-out is the
 * obvious shape and the wrong one: sign-out can be missed — a session revoked
 * from another device, a refresh token that expired, the process killed
 * mid-sign-out, a backup restored onto a different phone — and it is also
 * actively harmful, because it would re-download gigabytes for the ordinary
 * same-account sign-out and sign-in. Sign-out deliberately keeps the cache and
 * the files (`docs/android.md`, issue #573 gates the Auto surfaces instead);
 * comparing the recorded owner against the account actually signed in is what
 * catches every one of those paths. `LibraryCacheOwnerWiringTest` pins that.
 *
 * **The severe case is a change of server, not of user.** One Tandem's library
 * is shared: `EBook`/`AudioBook` carry no owner column and the list endpoints
 * are role-agnostic, so a second account on the same server would legitimately
 * see and fetch the same catalogue after its own refresh. Deleting there costs
 * a large re-download and buys no privacy. A different server is different in
 * kind — its row ids describe other books, and the cover cache is keyed by
 * audiobook id *alone* (`CoverArtHelper`: `filesDir/covers/{audiobookId}.jpg`),
 * so it would draw the previous server's artwork onto the new one's book 5.
 *
 * The recorded owner lives in the app's DataStore and is **not** removed by
 * `TokenManager.clearTokens` — it has to outlive the session or the next
 * sign-in has nothing to compare against. That store is excluded from backup
 * (issue #176), so a database restored onto another device reads no owner,
 * mismatches, and is cleared, which is the right default.
 *
 * One deliberate gap, the same trade issue #314 made for its legacy rows: an
 * install upgrading into this build has no recorded owner, so its existing cache
 * is adopted by the first account to sign in. In practice that is the account
 * already signed in, because `onAuthenticated()` runs at app start whenever a
 * token exists.
 */
@Singleton
class LibraryCacheOwner @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    private val tokenManager: TokenManager,
    private val serverUrlManager: ServerUrlManager,
    private val libraryCacheDao: LibraryCacheDao,
    @param:ApplicationContext private val context: Context,
    private val diagnosticLogger: DiagnosticLogger,
) {
    private val mutex = Mutex()

    /**
     * The owner this process has already reconciled to.
     *
     * Android Auto calls [reconcile] on every browse, every search and every
     * resolve; without this each of those would be a DataStore read on the
     * browse path, which has a four-second budget for the whole node.
     */
    @Volatile
    private var reconciled: Pair<String, Int>? = null

    /**
     * Bring the cache into line with the account that is signed in, and report
     * what that took.
     *
     * Never throws: this sits in front of the car's browse tree, and an
     * exception escaping into a Media3 callback leaves its future unset and the
     * head unit waiting. A failure is reported as [LibraryCacheReconcile
     * .Unverified] instead, which callers treat as "do not read".
     */
    suspend fun reconcile(): LibraryCacheReconcile = mutex.withLock {
        try {
            reconcileLocked()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            diagnosticLogger.e(LogChannel.APP, TAG, "could not verify the library cache owner", e)
            LibraryCacheReconcile.Unverified
        }
    }

    private suspend fun reconcileLocked(): LibraryCacheReconcile {
        val server = normalisedServer(serverUrlManager.currentUrl)
        val userId = tokenManager.currentUserId()
        // Signed out, or no server yet. Record nothing and clear nothing: the
        // cache is meant to survive sign-out, and guessing an owner here would
        // make the next sign-in believe the cache is already its own.
        if (server == null || userId == null) return LibraryCacheReconcile.Unchanged

        val current = server to userId
        if (reconciled == current) return LibraryCacheReconcile.Unchanged

        val prefs = dataStore.data.first()
        val storedServer = prefs[KEY_OWNER_SERVER]
        val storedUser = prefs[KEY_OWNER_USER]

        val outcome = when {
            // Nothing recorded — a fresh install, or one upgrading into this
            // build. Adopt, as issue #314 adopts its pre-scoping rows.
            storedServer == null || storedUser == null -> LibraryCacheReconcile.Unchanged
            storedServer == server && storedUser == userId -> LibraryCacheReconcile.Unchanged
            storedServer != server -> {
                clearForeignLibrary(storedServer)
                LibraryCacheReconcile.ServerChanged
            }
            else -> {
                log("account changed on $server ($storedUser -> $userId); the library is shared, keeping it")
                LibraryCacheReconcile.AccountChanged
            }
        }

        stamp(server, userId)
        reconciled = current
        return outcome
    }

    private suspend fun clearForeignLibrary(previousServer: String) {
        log("library cache belonged to $previousServer — clearing rows, downloads and covers")
        libraryCacheDao.clearLibrary()
        val removed = EBOOKS_DIR.emptyDir() + AUDIOBOOKS_DIR.emptyDir() + COVERS_DIR.emptyDir()
        log("cleared the library cache and $removed cached files")
    }

    /**
     * Empty one of the media directories.
     *
     * Wholesale rather than file-by-file from the rows. At a server change
     * *everything* in `files/ebooks`, `files/audiobooks` and `files/covers` came
     * from the outgoing server, and an earlier draft that deleted only what the
     * rows still flagged as downloaded was measurably worse: on a real device it
     * left three audiobooks behind whose rows had since been rewritten, with
     * nothing left that could ever name them again. Nothing here can catch a
     * download in flight for the new account — the stamp happens at sign-in,
     * before that account can start one.
     *
     * The covers directory has to go for a reason of its own: `CoverArtHelper`
     * caches at `filesDir/covers/{audiobookId}.jpg` — **id alone**, with no
     * server in the name — so another Tandem's artwork would be drawn onto this
     * one's book of the same id. It is cheap to rebuild; the next browse
     * refetches what it needs.
     */
    private fun String.emptyDir(): Int =
        File(context.filesDir, this).listFiles()?.count { it.delete() } ?: 0

    private suspend fun stamp(server: String, userId: Int) {
        dataStore.edit {
            it[KEY_OWNER_SERVER] = server
            it[KEY_OWNER_USER] = userId
        }
    }

    /** Same normalisation as [com.booksync.data.remote.UserScope.of], so the two agree. */
    private fun normalisedServer(url: String?): String? =
        url?.trim()?.trimEnd('/')?.takeIf { it.isNotEmpty() }

    private fun log(msg: String) = diagnosticLogger.i(LogChannel.APP, TAG, msg)
}
