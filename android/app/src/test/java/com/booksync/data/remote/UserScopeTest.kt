package com.booksync.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Issue #314. The cache partition key. Two users on one device must never share a
 * partition, and — the part easy to miss — nor must the same user id on two
 * different servers.
 */
class UserScopeTest {

    @Test
    fun `different users on one server are different scopes`() {
        assertNotEquals(
            UserScope.of("https://tandem.example.com", 1),
            UserScope.of("https://tandem.example.com", 2),
        )
    }

    @Test
    fun `the same user id on different servers is a different scope`() {
        // User 2 on one Tandem is not user 2 on another. Keying on the id alone
        // would let one server's rows be read as the other's, and its queued
        // writes replayed into the wrong account entirely.
        assertNotEquals(
            UserScope.of("https://tandem.example.com", 2),
            UserScope.of("https://other.example.com", 2),
        )
    }

    @Test
    fun `the same server and user is the same scope`() {
        assertEquals(
            UserScope.of("https://tandem.example.com", 2),
            UserScope.of("https://tandem.example.com", 2),
        )
    }

    @Test
    fun `a trailing slash does not split a scope in two`() {
        // ServerUrlManager normalises, but a stored value from an older build
        // might not. Splitting here would strand the previous rows in a scope
        // nothing ever reads again.
        assertEquals(
            UserScope.of("https://tandem.example.com", 2),
            UserScope.of("https://tandem.example.com/", 2),
        )
    }

    @Test
    fun `an unknown half yields no scope at all`() {
        // Callers must hold writes rather than attribute them to a guess.
        assertNull(UserScope.of("https://tandem.example.com", null))
        assertNull(UserScope.of(null, 2))
        assertNull(UserScope.of("", 2))
        assertNull(UserScope.of("   ", 2))
    }

    @Test
    fun `the legacy scope is distinct from every real one`() {
        assertNotEquals(UserScope.LEGACY, UserScope.of("https://tandem.example.com", 1))
        assertEquals("", UserScope.LEGACY.key)
    }
}
