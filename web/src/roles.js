/**
 * The role ladder, in one place (issue #359).
 *
 * It used to live inline in `contexts/AuthContext.jsx`, and five page tests
 * reimplemented the same expression in their `useAuth` mocks. That is how the
 * fail-open bug stayed invisible: a typo'd minimum behaved identically in the
 * mocks and in the app, so the suite stayed green either way.
 *
 * Keep this in step with `ROLE_HIERARCHY` in `server/models/user.py` and
 * `RoleGate` on Android — three copies of one ladder, and they must agree.
 */

export const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }

export const KNOWN_ROLES = Object.keys(ROLE_HIERARCHY)

/**
 * True if `userRole` meets or exceeds `minRole`.
 *
 * Fails closed on a minimum that is not a real role. The old form scored an
 * unrecognised minimum as 0 and everybody clears 0, so `min="admni"` did not
 * fail the check — it deleted it, silently, for every signed-in account. An
 * unknown *user* role is denied as it always was.
 */
export function roleMeets(userRole, minRole) {
    if (!Object.prototype.hasOwnProperty.call(ROLE_HIERARCHY, minRole)) return false
    return (ROLE_HIERARCHY[userRole] || 0) >= ROLE_HIERARCHY[minRole]
}
