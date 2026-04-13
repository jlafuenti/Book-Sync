import React from 'react'

/**
 * Mobile top app bar.
 * Props:
 *   title      — page title string
 *   onMenuOpen — callback to open MobileDrawer
 *   leftIcon   — optional: 'back' shows a back arrow instead of hamburger
 *   onBack     — callback when back arrow tapped
 *   rightActions — optional: React node(s) rendered in the right slot
 */
export default function MobileTopBar({ title, onMenuOpen, leftIcon, onBack, rightActions }) {
    return (
        <header className="mobile-top-bar mobile-only">
            <div className="topbar-left">
                {leftIcon === 'back' && (
                    <button className="topbar-btn" onClick={onBack} aria-label="Go back">
                        <span className="material-symbols-outlined">arrow_back</span>
                    </button>
                )}
                <span className="topbar-title">{title}</span>
            </div>
            <div className="topbar-right">
                {rightActions}
            </div>
        </header>
    )
}
