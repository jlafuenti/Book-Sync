import React, { createContext, useContext } from 'react'

const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }

const AuthContext = createContext({ user: null, hasMinRole: () => false })

export function AuthProvider({ user, children }) {
    function hasMinRole(minRole) {
        if (!user) return false
        return (ROLE_HIERARCHY[user.role] || 0) >= (ROLE_HIERARCHY[minRole] || 0)
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
