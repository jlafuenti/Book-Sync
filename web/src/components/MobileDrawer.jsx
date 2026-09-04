import React, { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { ThemePicker } from './ThemePicker'

const DRAWER_NAV = [
    { path: '/continue', label: 'Home', exact: true,
      icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></svg>
    },
    { path: '/library', label: 'Library',
      icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
    },
    { path: '/series', label: 'Series',
      icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /><path d="M12 2v20" /></svg>
    },
    { path: '/transcription', label: 'Transcription',
      icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" /></svg>
    },
    // Gated, and the destination depends on role -- the same rule the sidebar
    // and the bottom tab bar already follow (issues #283, #208). /system/status
    // is admin-only; Troubleshoot and Unsupported are editor-level. Leaving the
    // entry here for everyone sent a plain user into a redirect back to Home,
    // which reads as a broken app rather than as a permission boundary, and
    // pointed an editor at the one tab they cannot open.
    { path: '/system', label: 'System', minRole: 'editor',
      icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></svg>
    },
]

export default function MobileDrawer({ open, onClose, user, onLogout }) {
    const location = useLocation()
    const { hasMinRole } = useAuth()
    const drawerRef = useRef(null)
    const startXRef = useRef(null)

    const items = DRAWER_NAV.filter(item => !item.minRole || hasMinRole(item.minRole))
    const target = (item) => (
        item.path === '/system'
            ? (hasMinRole('admin') ? '/system/status' : '/system/troubleshoot')
            : item.path
    )

    const roleLabel = user?.role
        ? user.role.charAt(0).toUpperCase() + user.role.slice(1)
        : 'User'

    // Close on navigation
    useEffect(() => {
        if (open) onClose()
    }, [location.pathname]) // eslint-disable-line react-hooks/exhaustive-deps

    // Close on Escape
    useEffect(() => {
        if (!open) return
        const handleKey = (e) => { if (e.key === 'Escape') onClose() }
        document.addEventListener('keydown', handleKey)
        return () => document.removeEventListener('keydown', handleKey)
    }, [open, onClose])

    // Swipe-to-close
    const handleTouchStart = (e) => {
        startXRef.current = e.touches[0].clientX
    }

    const handleTouchEnd = (e) => {
        if (startXRef.current === null) return
        const delta = e.changedTouches[0].clientX - startXRef.current
        if (delta < -60) onClose() // Swipe left to close
        startXRef.current = null
    }

    const isActive = (item) => {
        if (item.exact) return location.pathname === item.path
        return location.pathname.startsWith(item.path)
    }

    return (
        <div
            className={`mobile-drawer-overlay ${open ? 'open' : ''}`}
            onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
        >
            <div
                className="mobile-drawer"
                ref={drawerRef}
                onTouchStart={handleTouchStart}
                onTouchEnd={handleTouchEnd}
            >
                <div className="mobile-drawer-header">
                    <div className="drawer-user">
                        <div className="drawer-avatar">
                            {user?.username?.[0]?.toUpperCase() || '?'}
                        </div>
                        <div className="drawer-user-info">
                            <div className="drawer-name">{user?.username || 'User'}</div>
                            <div className="drawer-role">{roleLabel}</div>
                        </div>
                    </div>
                </div>

                <nav>
                    {items.map(item => (
                        <Link
                            key={item.path}
                            to={target(item)}
                            className={`drawer-link ${isActive(item) ? 'active' : ''}`}
                        >
                            {item.icon}
                            <span>{item.label}</span>
                        </Link>
                    ))}
                </nav>

                <div className="mobile-drawer-footer">
                    <div className="drawer-theme-row">
                        <ThemePicker />
                    </div>
                    <button className="drawer-logout" onClick={onLogout}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                            <polyline points="16 17 21 12 16 7" />
                            <line x1="21" y1="12" x2="9" y2="12" />
                        </svg>
                        <span>Logout</span>
                    </button>
                </div>
            </div>
        </div>
    )
}
