package com.booksync.ui.account

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #205. These numbers are the Android third of a contract shared with
 * `server/schemas.py` and `web/src/lib/passwordPolicy.js`. Before this, the
 * sheet demanded 8 while the server and the web form allowed 6, so the three
 * surfaces disagreed about what a valid password was.
 */
class PasswordPolicyTest {

    @Test
    fun `policy is eight to one hundred twenty eight, matching the server`() {
        assertEquals(8, PasswordPolicy.MIN_LENGTH)
        assertEquals(128, PasswordPolicy.MAX_LENGTH)
    }

    @Test
    fun `message names both bounds`() {
        assertTrue(PasswordPolicy.MESSAGE.contains("8"))
        assertTrue(PasswordPolicy.MESSAGE.contains("128"))
    }

    @Test
    fun `one character under the floor is rejected`() {
        assertFalse(PasswordPolicy.isValid("a".repeat(PasswordPolicy.MIN_LENGTH - 1)))
    }

    @Test
    fun `exactly at the floor is accepted`() {
        assertTrue(PasswordPolicy.isValid("a".repeat(PasswordPolicy.MIN_LENGTH)))
    }

    @Test
    fun `exactly at the ceiling is accepted`() {
        assertTrue(PasswordPolicy.isValid("a".repeat(PasswordPolicy.MAX_LENGTH)))
    }

    @Test
    fun `one character over the ceiling is rejected`() {
        assertFalse(PasswordPolicy.isValid("a".repeat(PasswordPolicy.MAX_LENGTH + 1)))
    }

    @Test
    fun `an empty password is rejected`() {
        assertFalse(PasswordPolicy.isValid(""))
    }

    @Test
    fun `errorFor returns the message for a bad password and null for a good one`() {
        assertEquals(PasswordPolicy.MESSAGE, PasswordPolicy.errorFor("short"))
        assertNull(PasswordPolicy.errorFor("a".repeat(PasswordPolicy.MIN_LENGTH)))
    }
}
