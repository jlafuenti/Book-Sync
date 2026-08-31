import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

const NAV_ITEMS = [
    { path: '/continue', label: 'Home', icon: 'home', exact: true },
    { path: '/library', label: 'Library', icon: 'auto_stories' },
    { path: '/series', label: 'Series', icon: 'subscriptions' },
    { path: '/transcription', label: 'Transcription', icon: 'settings_voice' },
    // Gated, and the destination depends on role: /system/status is admin-only
    // while Troubleshoot and Unsupported are editor-level (issue #283). Sending
    // an editor to status would bounce them straight back out.
    { path: '/system', label: 'Admin', icon: 'admin_panel_settings', minRole: 'editor' },
]

export default function BottomNavBar() {
    const location = useLocation()
    const { hasMinRole } = useAuth()
    const items = NAV_ITEMS.filter(item => !item.minRole || hasMinRole(item.minRole))
    const target = (item) => (
        item.path === '/system'
            ? (hasMinRole('admin') ? '/system/status' : '/system/troubleshoot')
            : item.path
    )

    const isActive = (item) => {
        if (item.exact) return location.pathname === item.path
        return location.pathname.startsWith(item.path)
    }

    return (
        <nav className="mobile-bottom-nav mobile-only">
            {items.map(item => (
                <Link
                    key={item.path}
                    to={target(item)}
                    className={`nav-tab ${isActive(item) ? 'active' : ''}`}
                >
                    <span
                        className="nav-icon material-symbols-outlined"
                        style={isActive(item) ? { fontVariationSettings: "'FILL' 1" } : undefined}
                    >
                        {item.icon}
                    </span>
                    <span className="nav-label">{item.label}</span>
                </Link>
            ))}
        </nav>
    )
}
