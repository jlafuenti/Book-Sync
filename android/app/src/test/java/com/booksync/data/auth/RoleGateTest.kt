package com.booksync.data.auth

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which roles may edit the library (issue #170).
 *
 * Android had **no role concept at all**: `grep -rn "\brole\b"` over the app
 * matched one comment. So a plain `user` saw "Unlink pair" and "Pair" as
 * ordinary actions, tapped one, the server answered 403, and the snackbar
 * showed Retrofit's raw `"HTTP 403 "`. The web never renders those controls for
 * that user. Worse than the cosmetics: with no notion of a role, any admin
 * action added to Android later ships ungated by default.
 *
 * This is the **third** copy of a ladder that already exists identically on the
 * server (`models/user.py:13`) and the web (`AuthContext.jsx:3`):
 *
 *     {superadmin: 4, admin: 3, editor: 2, user: 1}
 *
 * so the values are pinned here rather than left to drift. If the server ever
 * gains a role, this test is what fails first.
 *
 * Unknown and null both fail closed — an unrecognised role is not a licence.
 */
class RoleGateTest {

    @Test
    fun `the ladder matches the server and the web`() {
        // Each role satisfies its own level and everything below it.
        assertTrue(hasMinRole("superadmin", "admin"))
        assertTrue(hasMinRole("superadmin", "editor"))
        assertTrue(hasMinRole("admin", "editor"))
        assertTrue(hasMinRole("editor", "editor"))
        assertTrue(hasMinRole("editor", "user"))
        assertTrue(hasMinRole("user", "user"))
    }

    @Test
    fun `a plain user cannot edit`() {
        // The actual bug: this user was shown Unlink pair and Pair.
        assertFalse(hasMinRole("user", "editor"))
        assertFalse(hasMinRole("user", "admin"))
    }

    @Test
    fun `an editor is not an admin`() {
        assertFalse(hasMinRole("editor", "admin"))
        assertFalse(hasMinRole("admin", "superadmin"))
    }

    @Test
    fun `an unknown or missing role fails closed`() {
        // A role the client does not recognise must not grant anything. This is
        // the case that matters if the server adds a role and an older app has
        // never heard of it.
        assertFalse(hasMinRole(null, "user"))
        assertFalse(hasMinRole("", "user"))
        assertFalse(hasMinRole("librarian", "user"))
        assertFalse(hasMinRole("SUPERADMIN", "editor"))
    }

    @Test
    fun `an unknown minimum is not accidentally satisfiable`() {
        // Guards a typo at a call site: hasMinRole(role, "editer") must not
        // silently pass for everyone because the unknown minimum scores zero.
        assertFalse(hasMinRole("superadmin", "editer"))
        assertFalse(hasMinRole("user", "editer"))
    }
}
