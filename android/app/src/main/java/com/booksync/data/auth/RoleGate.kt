package com.booksync.data.auth

/**
 * The role ladder, matching `server/models/user.py` and
 * `web/src/contexts/AuthContext.jsx` exactly. Three copies of one contract, so
 * `RoleGateTest` pins the values: if the server gains a role, that test is what
 * fails first.
 */
private val ROLE_HIERARCHY = mapOf(
    "superadmin" to 4,
    "admin" to 3,
    "editor" to 2,
    "user" to 1,
)

/**
 * Whether [role] is at least [minimumRole] (issue #170).
 *
 * Android had no role concept at all, so a plain `user` was shown "Unlink pair"
 * and "Pair", tapped one, and got Retrofit's raw `"HTTP 403 "` back from the
 * editor-gated endpoint. The web never renders those controls for that user.
 *
 * Unknown and null roles fail closed — an unrecognised role is not a licence,
 * which matters when the server adds a role an older client has never heard of.
 *
 * **One deliberate difference from the server and the web.** Both do
 * `ROLE_HIERARCHY.get(min, 0)`, so an unknown *minimum* scores zero and every
 * caller satisfies it. Here an unrecognised minimum returns false instead. A
 * typo like `hasMinRole(role, "editer")` is a programmer error either way; the
 * question is which way it fails. Failing open would show an editor-only
 * control to everyone — precisely the bug this issue is about, and invisible
 * until someone reports a 403. Failing closed hides a control from an admin,
 * which gets noticed immediately. Narrow, and only reachable through a typo.
 */
fun hasMinRole(role: String?, minimumRole: String): Boolean {
    val have = ROLE_HIERARCHY[role] ?: return false
    val need = ROLE_HIERARCHY[minimumRole] ?: return false
    return have >= need
}

/**
 * A message worth showing a user for a failed library action (issue #170).
 *
 * The snackbar used to render `e.message` verbatim, so a permission failure read
 * `"HTTP 403 "` — Retrofit's `HttpException.message`. Gating the controls means
 * most users never reach a 403 now, but this still covers the window before the
 * role is known, a role that changed server-side mid-session, and any endpoint
 * gated more tightly than the UI knows about.
 */
fun userFacingError(e: Throwable): String {
    val text = e.message.orEmpty()
    return when {
        text.contains("403") -> "You don't have permission to change the library."
        text.contains("401") -> "Your session has expired. Sign in again."
        text.isBlank() -> "Action failed"
        else -> text
    }
}
