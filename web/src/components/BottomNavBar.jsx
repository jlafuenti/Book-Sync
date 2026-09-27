import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { useTourAnchor, TourAnchors } from '../tour/anchors'

const NAV_ITEMS = [
    { path: '/continue', label: 'Home', icon: 'home', exact: true },
    { path: '/library', label: 'Library', icon: 'auto_stories', anchor: TourAnchors.NavLibrary },
    { path: '/series', label: 'Series', icon: 'subscriptions', anchor: TourAnchors.NavSeries },
    { path: '/transcription', label: 'Transcription', icon: 'settings_voice', anchor: TourAnchors.NavTranscription },
    // Gated, and the destination depends on role: /system/status is admin-only
    // while Troubleshoot and Unsupported are editor-level (issue #283). Sending
    // an editor to status would bounce them straight back out.
    { path: '/system', label: 'Admin', icon: 'admin_panel_settings', minRole: 'editor', anchor: TourAnchors.NavSystem },
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

    // The mobile mirror of the sidebar's five tour anchors (App.jsx) — this is
    // what makes a "click Library/Series/Transcription/System" tapAnchor step
    // work under 768px, where the sidebar itself is display:none. NavSystem is
    // additionally gated so its anchor never lingers registered once the item
    // (already role-filtered out of `items` above) stops being rendered.
    const libraryAnchorRef = useTourAnchor(TourAnchors.NavLibrary)
    const seriesAnchorRef = useTourAnchor(TourAnchors.NavSeries)
    const transcriptionAnchorRef = useTourAnchor(TourAnchors.NavTranscription)
    const systemAnchorRef = useTourAnchor(TourAnchors.NavSystem, { enabled: hasMinRole('editor') })
    const anchorRefs = {
        [TourAnchors.NavLibrary]: libraryAnchorRef,
        [TourAnchors.NavSeries]: seriesAnchorRef,
        [TourAnchors.NavTranscription]: transcriptionAnchorRef,
        [TourAnchors.NavSystem]: systemAnchorRef,
    }

    return (
        <nav className="mobile-bottom-nav mobile-only">
            {items.map(item => (
                <Link
                    key={item.path}
                    to={target(item)}
                    ref={item.anchor ? anchorRefs[item.anchor] : undefined}
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
