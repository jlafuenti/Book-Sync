import React from 'react'
import { Link, useLocation } from 'react-router-dom'

const NAV_ITEMS = [
    { path: '/continue', label: 'Home', icon: 'home', exact: true },
    { path: '/library', label: 'Library', icon: 'auto_stories' },
    { path: '/series', label: 'Series', icon: 'subscriptions' },
    { path: '/transcription', label: 'Transcription', icon: 'settings_voice' },
    { path: '/system', label: 'Admin', icon: 'admin_panel_settings' },
]

export default function BottomNavBar() {
    const location = useLocation()

    const isActive = (item) => {
        if (item.exact) return location.pathname === item.path
        return location.pathname.startsWith(item.path)
    }

    return (
        <nav className="mobile-bottom-nav mobile-only">
            {NAV_ITEMS.map(item => (
                <Link
                    key={item.path}
                    to={item.path}
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
