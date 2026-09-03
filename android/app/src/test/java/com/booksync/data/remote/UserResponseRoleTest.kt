package com.booksync.data.remote

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * `/auth/me` carries the caller's role, and Android was throwing it away
 * (issue #170).
 *
 * The server has always sent it (`server/schemas.py:50`, `role: str`), but the
 * Android `UserResponse` had no such field and the shared `Json` is built with
 * `ignoreUnknownKeys = true` — so it was dropped silently, and the app had no
 * way to know a user could not edit. Nothing failed; the information simply
 * never arrived.
 *
 * The default matters as much as the field. An older server that does not send
 * `role` must decode to `"user"`, the least-privileged value, so the app fails
 * closed rather than assuming edit rights.
 */
class UserResponseRoleTest {

    private val json = Json { ignoreUnknownKeys = true }

    private fun body(roleField: String) = """
        {
          "id": 2,
          "username": "claude",
          "email": "claude@example.invalid",
          $roleField
          "is_admin": false,
          "is_active": true,
          "theme": "blueprint",
          "created_at": "2026-01-01T00:00:00Z"
        }
    """.trimIndent()

    @Test
    fun `the role is decoded, not dropped`() {
        assertEquals("editor", json.decodeFromString<UserResponse>(body("\"role\": \"editor\",")).role)
        assertEquals("admin", json.decodeFromString<UserResponse>(body("\"role\": \"admin\",")).role)
        assertEquals("user", json.decodeFromString<UserResponse>(body("\"role\": \"user\",")).role)
    }

    @Test
    fun `a server that omits the role is treated as least privileged`() {
        // An older server, or a response shape that changes underneath us. The
        // safe assumption is "cannot edit", not "can".
        assertEquals("user", json.decodeFromString<UserResponse>(body("")).role)
    }

    @Test
    fun `an unfamiliar role decodes but grants nothing`() {
        // Decoding must not throw on a role this client has never heard of —
        // that would break login entirely against a newer server. It just must
        // not confer any rights, which RoleGate enforces.
        val decoded = json.decodeFromString<UserResponse>(body("\"role\": \"librarian\","))
        assertEquals("librarian", decoded.role)
        assertEquals(false, com.booksync.data.auth.hasMinRole(decoded.role, "user"))
    }
}
