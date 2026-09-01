package com.booksync.data.remote

import com.booksync.data.local.dao.ScopeAdoptionDao
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.junit.Test

/**
 * Issue #314. This class had no tests at all in the first cut, and that is exactly
 * why it shipped with adoption gated on `hasLegacyBookmarks()` — a check that
 * strands the other four tables for anyone whose `bookmarks` happens to be empty.
 */
class UserScopeProviderTest {

    /** Seeds run off the constructor since issue #318. */
    private val seedScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)


    private val adoption = mockk<ScopeAdoptionDao>(relaxed = true)

    private fun provider(server: String? = "https://tandem.example.com", token: String? = TOKEN_USER_2)
        : UserScopeProvider {
        val tokenManager = mockk<TokenManager>(relaxed = true)
        every { tokenManager.getAccessToken() } returns flowOf(token)
        coEvery { tokenManager.currentUserId() } returns userIdFromAccessToken(token)
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns (server ?: "")
        return UserScopeProvider(tokenManager, serverUrlManager, adoption, seedScope)
    }

    @Test
    fun `the scope is resolved at construction, before anything reads the cache`() {
        // Read synchronously from non-suspend Flow builders and from the player
        // service; a null window here would show an empty library on cold start.
        assertEquals("https://tandem.example.com|2", provider().currentKey)
    }

    @Test
    fun `no resolvable account yields no scope rather than a guess`() {
        assertNull(provider(token = null).currentKey)
        assertNull(provider(server = null).currentKey)
        assertNull(provider(token = "not-a-jwt").currentKey)
    }

    @Test
    fun `adoption is not gated on any single table`() = runBlocking {
        // The bug this test exists for: gating on `bookmarks` strands a
        // standalone-audiobook listener's `user_progress` rows — they never have a
        // bookmark, so their positions would stay invisible and unsyncable forever.
        provider().onAuthenticated()

        coVerify(exactly = 1) { adoption.adoptAll("https://tandem.example.com|2") }
    }

    @Test
    fun `a failed adoption does not take down the caller`() = runBlocking {
        // Runs on the launch path and inside login. An exception here used to mean
        // a crash on every start, or a sign-in that could not complete.
        coEvery { adoption.adoptAll(any()) } throws IllegalStateException("constraint")

        provider().onAuthenticated()   // must not throw

        assertEquals("https://tandem.example.com|2", provider().currentKey)
    }

    @Test
    fun `nothing is adopted when there is no account to adopt into`() = runBlocking {
        provider(token = null).onAuthenticated()

        coVerify(exactly = 0) { adoption.adoptAll(any()) }
    }

    private companion object {
        // {"sub":"2","type":"access","ver":0}
        const val TOKEN_USER_2 =
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIyIiwidHlwZSI6ImFjY2VzcyIsInZlciI6MH0.sig"
    }
}
