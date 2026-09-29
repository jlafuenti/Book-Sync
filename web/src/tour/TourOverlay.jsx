import { useContext, useEffect, useRef, useState } from 'react'
import { TourRegistryContext } from './anchors'
import { useTour } from './TourContext'
import useIsMobile from '../hooks/useIsMobile'
import './tour.css'

const FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'textarea:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(', ')

function focusableIn(node) {
    if (!node) return []
    return Array.from(node.querySelectorAll(FOCUSABLE))
}

/**
 * The guided walkthrough's spotlight-and-card layer (issue #598, Track A).
 *
 * Reads its state from `useTour()` (the controller `TourProvider` owns) and
 * the live anchor rect from `TourRegistryContext` — a rect can move under
 * scroll/resize even while the resolution itself stays Found, so this
 * subscribes to the registry directly rather than trusting a snapshot.
 *
 * Renders nothing while idle/finished. Otherwise: four transparent blockers
 * framing the spotlighted control (or one full-viewport blocker when there
 * is no hole to cut), a `.tour-hole` painting the dimmed scrim via
 * `box-shadow` with `pointer-events: none` so the real control underneath
 * still receives the click, and the card itself.
 */
export default function TourOverlay() {
    const { state, next, back, quit, skip } = useTour()
    const registry = useContext(TourRegistryContext)
    const isMobile = useIsMobile()
    const cardRef = useRef(null)
    const [, forceTick] = useState(0)

    const active = state.status === 'running' || state.status === 'preparing'

    // The anchor's rect can change (scroll, resize, layout) without the
    // controller's resolution changing; re-render on every registry change
    // so the hole/blockers track it live.
    useEffect(() => {
        if (!active) return undefined
        return registry.subscribe(() => forceTick((t) => t + 1))
    }, [active, registry])

    useEffect(() => {
        if (!active) return undefined
        const onKeyDown = (event) => {
            if (event.key === 'Escape') {
                event.stopPropagation()
                quit()
                return
            }
            if (event.key !== 'Tab') return
            const node = cardRef.current
            if (!node) return
            const items = focusableIn(node)
            if (items.length === 0) return
            const first = items[0]
            const last = items[items.length - 1]
            const activeEl = document.activeElement
            const outside = !node.contains(activeEl)
            if (event.shiftKey) {
                if (activeEl === first || outside) {
                    event.preventDefault()
                    last.focus()
                }
            } else if (activeEl === last || outside) {
                event.preventDefault()
                first.focus()
            }
        }
        document.addEventListener('keydown', onKeyDown, true)
        return () => document.removeEventListener('keydown', onKeyDown, true)
    }, [active, quit])

    useEffect(() => {
        if (!active) return
        const node = cardRef.current
        const target = focusableIn(node)[0] || node
        target?.focus()
    }, [active, state.index, state.resolution])

    if (!active || !state.step) return null

    const { step, resolution, index, total, canGoBack, willCleanUp } = state
    const rect = step.anchor ? registry.get(step.anchor) : null
    const hasHole = resolution === 'found' && !!rect

    let title = step.title
    let body = step.body
    let showHint = false
    if (resolution === 'pending') {
        title = 'One moment…'
        body = null
    } else if (resolution === 'missing') {
        body = step.emptyBody || step.body
    } else {
        showHint = step.advance.kind === 'tapAnchor'
        if (step.id === 'done' && willCleanUp && step.cleanUpSuffix) {
            body = body + step.cleanUpSuffix
        }
    }

    let advanceButton = null
    if (resolution !== 'pending') {
        if (step.advance.kind === 'next') {
            advanceButton = <button className="btn btn-primary" onClick={next}>Next</button>
        } else if (step.advance.kind === 'finish') {
            advanceButton = <button className="btn btn-primary" onClick={next}>Done</button>
        } else if (step.advance.kind === 'waitFor' && step.advance.skippable) {
            advanceButton = <button className="btn btn-secondary" onClick={skip}>Skip</button>
        }
    }

    // On mobile the bottom dock is the layout: a card placed beside a
    // section under the fold would itself land off-screen.
    const cardStyle = hasHole && !isMobile ? cardPositionStyle(rect) : undefined
    const cardClassName = [
        'tour-card',
        resolution === 'pending' ? 'tour-card--pending' : '',
        isMobile ? 'tour-card--mobile' : '',
        // The dock must not cover the hole: a control in the lower half
        // (the bottom nav bar) puts the card at the top instead.
        isMobile && hasHole && (rect.top + rect.bottom) / 2 > viewportHeight() / 2 ? 'tour-card--mobile-top' : '',
        !hasHole && !isMobile ? 'tour-card--centered' : '',
    ].filter(Boolean).join(' ')

    return (
        <div className="tour-overlay" data-testid="tour-overlay">
            {hasHole ? (
                <>
                    <div className="tour-blocker" style={{ top: 0, left: 0, right: 0, height: rect.top }} data-testid="tour-blocker-top" />
                    <div className="tour-blocker" style={{ top: rect.bottom, left: 0, right: 0, bottom: 0 }} data-testid="tour-blocker-bottom" />
                    <div className="tour-blocker" style={{ top: rect.top, left: 0, width: rect.left, height: rect.height }} data-testid="tour-blocker-left" />
                    <div className="tour-blocker" style={{ top: rect.top, left: rect.right, right: 0, height: rect.height }} data-testid="tour-blocker-right" />
                    <div
                        className="tour-hole"
                        data-testid="tour-hole"
                        style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
                    />
                </>
            ) : (
                <div className="tour-blocker tour-blocker--full" data-testid="tour-blocker-full" />
            )}

            <div
                ref={cardRef}
                role="dialog"
                aria-modal="true"
                aria-label={title}
                tabIndex={-1}
                className={cardClassName}
                style={cardStyle}
            >
                <div className="tour-card-header">
                    <h3 className="tour-card-title">{title}</h3>
                    <button
                        className="tour-card-quit"
                        aria-label="Quit the walkthrough"
                        onClick={quit}
                    >
                        &times;
                    </button>
                </div>
                {body && <p className="tour-card-body">{body}</p>}
                {showHint && <p className="tour-card-hint">Tap the highlighted control</p>}
                <div className="tour-card-footer">
                    <span className="tour-card-progress">{index + 1} of {total}</span>
                    <div className="tour-card-actions">
                        {canGoBack && (
                            <button className="btn btn-secondary" onClick={back}>Back</button>
                        )}
                        {advanceButton}
                    </div>
                </div>
            </div>
        </div>
    )
}

// Centers the card near the spotlighted control without covering it: below
// when there's room, above otherwise, and clamped so it never runs off the
// viewport edges (a fixed 320px-wide card, see tour.css).
function viewportHeight() {
    return typeof window !== 'undefined' ? window.innerHeight : 768
}

function cardPositionStyle(rect) {
    const cardWidth = 320
    const margin = 12
    const viewportW = typeof window !== 'undefined' ? window.innerWidth : 1024
    const viewportH = typeof window !== 'undefined' ? window.innerHeight : 768
    let left = rect.left
    left = Math.max(margin, Math.min(left, viewportW - cardWidth - margin))
    // Below the hole when there is room, else above it, else — the hole
    // fills the viewport, as the reader page does — docked inside the
    // viewport at its bottom edge rather than pushed off the top.
    const spaceBelow = viewportH - rect.bottom
    if (spaceBelow >= 160) return { left, top: rect.bottom + margin, bottom: undefined }
    if (rect.top >= 160 + margin) return { left, top: undefined, bottom: viewportH - rect.top + margin }
    return { left, top: undefined, bottom: margin }
}
