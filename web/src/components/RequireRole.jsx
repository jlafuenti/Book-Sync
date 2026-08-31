import { Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

/**
 * Route-level role gate (issue #283).
 *
 * The /system routes were plain <Route> entries, so any signed-in user could
 * type the URL, get the Admin Console shell, and have it fire its
 * admin-flavoured reads. No privilege was gained — every mutating endpoint was
 * already gated server-side — but disk capacity for all three data roots,
 * backup health, and internal hostnames were visible to anyone with an account.
 *
 * This is defence in depth and nothing more. The role lives in a JWT the client
 * cannot be trusted to police, so the barrier is the matching
 * `Depends(get_admin_user)` / `get_editor_user` on the server; this only stops
 * the app presenting a console the user cannot use, and stops it firing reads
 * that would 403.
 *
 * Redirects rather than showing a "forbidden" page: below-role users are not
 * meant to know the route is there.
 */
export function RequireRole({ min, children }) {
    const { hasMinRole } = useAuth()
    if (!hasMinRole(min)) {
        return <Navigate to="/continue" replace />
    }
    return children
}

export default RequireRole
