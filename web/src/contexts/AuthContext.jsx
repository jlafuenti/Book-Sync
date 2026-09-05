import React, { createContext, useContext } from 'react'
import { roleMeets } from '../roles'

const AuthContext = createContext({ user: null, hasMinRole: () => false })

export function AuthProvider({ user, children }) {
    // The ladder itself lives in `src/roles.js` so the page tests can mock
    // `useAuth` with the real comparison instead of a copy of it (issue #359).
    function hasMinRole(minRole) {
        if (!user) return false
        return roleMeets(user.role, minRole)
    }

    return (
        <AuthContext.Provider value={{ user, hasMinRole }}>
            {children}
        </AuthContext.Provider>
    )
}

export function useAuth() {
    return useContext(AuthContext)
}
