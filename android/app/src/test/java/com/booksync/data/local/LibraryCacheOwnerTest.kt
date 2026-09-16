package com.booksync.data.local

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import com.booksync.data.local.dao.LibraryCacheDao
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.diagnostics.DiagnosticLogger
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * Whose books the local library cache holds, and what happens when that stops
 * being the account signing in (issue #575).
 *
 * Issue #314 partitioned the *reading* tables by `(server, user)`. The library
 * tables — `book_pairs`, `ebooks`, `audiobooks` — were left global, and nothing
 * refreshed or cleared them at login, so from the moment a second account signed
 * in until it opened the Library screen against a reachable server the phone and
 * the car listed the previous account's books, the voice index contained them,
 * and a *downloaded* one played from the local file with no token involved.
 * Offline, that window never closed.
 *
 * **The check is on state, not on an event.** A sign-out hook would be the
 * obvious place, and it is the wrong one: sign-out can be missed — a session
 * revoked from another device, a refresh token that expired, the process killed
 * mid-sign-out, a backup restored onto another phone. Comparing the cache's
 * recorded owner against the account that is actually signed in catches all of
 * those. That is the same failure shape as #573, where the safety step existed
 * but nothing ever reached it, so the headline test below signs in as a second
 * account with **no sign-out in between**.
 *
 * **The severe case is a server change.** One Tandem's library is shared by
 * every account on it — `EBook`/`AudioBook` carry no owner column and the list
 * endpoints are role-agnostic — so a second account on the same server would
 * legitimately see and fetch the same catalogue after its own refresh, and
 * deleting there costs a large re-download and buys no privacy. A different
 * server is different in kind: the ids collide, so its rows describe other
 * books entirely and its cover cache (keyed by audiobook id alone) draws the
 * wrong artwork.
 */
class LibraryCacheOwnerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val storeScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val keyServer = stringPreferencesKey("library_cache_owner_server")
    private val keyUser = intPreferencesKey("library_cache_owner_user")

    private lateinit var dataStore: DataStore<Preferences>
    private lateinit var filesDir: File
    private lateinit var dao: LibraryCacheDao

    private val tokenManager = mockk<TokenManager>(relaxed = true)
    private val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)

    @Before
    fun setUp() {
        dataStore = PreferenceDataStoreFactory.create(scope = storeScope) {
            tmp.newFile("owner.preferences_pb")
        }
        filesDir = tmp.newFolder("files")
        dao = mockk(relaxed = true)
    }

    @After
    fun tearDown() {
        storeScope.cancel()
    }

    // --- fixtures -----------------------------------------------------------

    private fun owner(server: String? = "https://a.example.com", user: Int? = 1) {
        every { serverUrlManager.currentUrl } returns (server ?: "")
        coEvery { tokenManager.currentUserId() } returns user
    }

    private suspend fun storeOwner(server: String, user: Int) {
        dataStore.edit {
            it[keyServer] = server
            it[keyUser] = user
        }
    }

    private suspend fun storedOwner(): Pair<String?, Int?> =
        dataStore.data.first().let { it[keyServer] to it[keyUser] }

    private fun build() = LibraryCacheOwner(
        dataStore = dataStore,
        tokenManager = tokenManager,
        serverUrlManager = serverUrlManager,
        libraryCacheDao = dao,
        context = mockk<Context>(relaxed = true).also { every { it.filesDir } returns filesDir },
        diagnosticLogger = mockk<DiagnosticLogger>(relaxed = true),
    )

    private fun writeFile(dir: String, name: String): File =
        File(File(filesDir, dir).also { it.mkdirs() }, name).also { it.writeText("bytes") }

    // --- the headline: state, not an event ----------------------------------

    /**
     * The issue's own repro, minus the sign-out. Nothing here calls a logout
     * path; the cache simply belongs to a server the signed-in account is not
     * on, which is all the check gets to see after a revoked session or a
     * restored backup.
     */
    @Test
    fun `a cache owned by another server is cleared at sign-in with no sign-out in between`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)

        assertEquals(LibraryCacheReconcile.ServerChanged, build().reconcile())

        coVerify(exactly = 1) { dao.clearLibrary() }
        assertEquals("https://b.example.com" to 1, storedOwner())
    }

    @Test
    fun `the same account changes nothing`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://a.example.com", user = 1)
        val kept = writeFile("audiobooks", "mine.m4b")

        assertEquals(LibraryCacheReconcile.Unchanged, build().reconcile())

        coVerify(exactly = 0) { dao.clearLibrary() }
        assertTrue("a re-login must not cost the user their downloads", kept.exists())
    }

    // --- same server, different user: tidiness, not a breach -----------------

    @Test
    fun `a different user on the same server keeps the rows and the files`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://a.example.com", user = 2)
        val shared = writeFile("audiobooks", "shared.m4b")

        assertEquals(LibraryCacheReconcile.AccountChanged, build().reconcile())

        coVerify(exactly = 0) { dao.clearLibrary() }
        assertTrue(
            "one server's library is shared by every account on it, so deleting " +
                "here costs a large re-download and buys no privacy",
            shared.exists(),
        )
        assertEquals("https://a.example.com" to 2, storedOwner())
    }

    // --- server change: thorough ---------------------------------------------

    @Test
    fun `a server change deletes the outgoing account's downloads`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)
        val files = listOf(
            writeFile("ebooks", "standalone.epub"),
            writeFile("audiobooks", "standalone.m4b"),
            writeFile("ebooks", "paired.epub"),
            writeFile("audiobooks", "paired.m4b"),
        )

        build().reconcile()

        for (f in files) assertFalse("${f.name} belonged to the other server", f.exists())
    }

    /**
     * An earlier draft deleted only what the rows still flagged as downloaded.
     * Run against a real device that left three audiobooks behind whose rows had
     * since been rewritten — and once the rows are gone, nothing can ever name
     * them again. At a server change everything in these directories came from
     * the outgoing server, so everything in them goes.
     */
    @Test
    fun `a server change removes a file no row claimed`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)
        val orphan = writeFile("audiobooks", "no-row-claims-me.m4b")

        build().reconcile()

        assertFalse("leaving it behind is a file nothing can ever reach or remove", orphan.exists())
    }

    @Test
    fun `a server change leaves the tokens alone`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)
        val datastore = writeFile("datastore", "booksync_prefs.preferences_pb")

        build().reconcile()

        assertTrue(
            "only the three media directories are the library cache's to empty",
            datastore.exists(),
        )
    }

    /**
     * `CoverArtHelper` caches at `filesDir/covers/{audiobookId}.jpg` — **id
     * alone**, with no server in the name. Across servers that draws the
     * previous account's artwork onto the new account's book 5, whatever the
     * rows say.
     */
    @Test
    fun `a server change clears the cover cache, which is keyed by id alone`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)
        val cover = writeFile("covers", "5.jpg")

        build().reconcile()

        assertFalse("id 5 means a different book on a different server", cover.exists())
    }

    // --- states that must do nothing -----------------------------------------

    @Test
    fun `a first run records the owner without clearing`() = runBlocking {
        owner(server = "https://a.example.com", user = 1)

        assertEquals(LibraryCacheReconcile.Unchanged, build().reconcile())

        coVerify(exactly = 0) { dao.clearLibrary() }
        assertEquals("https://a.example.com" to 1, storedOwner())
    }

    @Test
    fun `signed out records nothing and clears nothing`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://a.example.com", user = null)
        val kept = writeFile("audiobooks", "mine.m4b")

        assertEquals(LibraryCacheReconcile.Unchanged, build().reconcile())

        coVerify(exactly = 0) { dao.clearLibrary() }
        assertTrue("sign-out keeps the cache — the Auto surfaces are gated instead", kept.exists())
        assertEquals(
            "the owner must outlive the session, or the next sign-in has nothing to compare",
            "https://a.example.com" to 1,
            storedOwner(),
        )
    }

    @Test
    fun `no server configured records nothing`() = runBlocking {
        owner(server = null, user = 1)

        assertEquals(LibraryCacheReconcile.Unchanged, build().reconcile())

        assertEquals(null to null, storedOwner())
    }

    @Test
    fun `a trailing slash is not a different server`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://a.example.com/", user = 1)

        assertEquals(LibraryCacheReconcile.Unchanged, build().reconcile())

        coVerify(exactly = 0) { dao.clearLibrary() }
    }

    // --- failure is not permission --------------------------------------------

    /**
     * If the owner cannot be established the answer is not "carry on": the
     * callers turn [LibraryCacheReconcile.Unverified] into an empty browse node
     * and a refused resolve, so an unreadable owner never renders as somebody
     * else's library.
     */
    @Test
    fun `a failure to read the owner reports Unverified rather than throwing`() = runBlocking {
        owner(server = "https://a.example.com", user = 1)
        coEvery { tokenManager.currentUserId() } throws IllegalStateException("datastore is gone")

        assertEquals(LibraryCacheReconcile.Unverified, build().reconcile())
    }

    @Test
    fun `a failure while clearing reports Unverified rather than stamping the new owner`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)
        coEvery { dao.clearLibrary() } throws IllegalStateException("disk full")

        assertEquals(LibraryCacheReconcile.Unverified, build().reconcile())

        assertEquals(
            "stamping an owner whose clear failed would make the next run believe " +
                "the cache is already the new account's",
            "https://a.example.com" to 1,
            storedOwner(),
        )
    }

    // --- cheap to call from everywhere ----------------------------------------

    @Test
    fun `repeat calls do not re-read or re-clear`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://b.example.com", user = 1)
        val subject = build()

        assertEquals(LibraryCacheReconcile.ServerChanged, subject.reconcile())
        assertEquals(LibraryCacheReconcile.Unchanged, subject.reconcile())
        assertEquals(LibraryCacheReconcile.Unchanged, subject.reconcile())

        coVerify(exactly = 1) { dao.clearLibrary() }
    }

    @Test
    fun `the owner is re-resolved after the account changes within one process`() = runBlocking {
        storeOwner("https://a.example.com", 1)
        owner(server = "https://a.example.com", user = 1)
        val subject = build()
        assertEquals(LibraryCacheReconcile.Unchanged, subject.reconcile())

        owner(server = "https://b.example.com", user = 1)

        assertEquals(LibraryCacheReconcile.ServerChanged, subject.reconcile())
        coVerify(exactly = 1) { dao.clearLibrary() }
    }

    @Test
    fun `a null owner is never mistaken for a recorded one`() = runBlocking {
        dataStore.edit { it[keyServer] = "https://a.example.com" }  // user id missing
        owner(server = "https://b.example.com", user = 1)

        assertEquals(LibraryCacheReconcile.Unchanged, build().reconcile())

        coVerify(exactly = 0) { dao.clearLibrary() }
        assertNull(
            "half an owner is not an owner; record the whole one and move on",
            storedOwner().first?.takeIf { it != "https://b.example.com" },
        )
    }
}
