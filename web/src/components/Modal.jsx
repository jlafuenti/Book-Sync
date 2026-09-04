import { useEffect, useRef } from 'react'

/**
 * The one modal primitive (issue #279).
 *
 * Before this, every dialog in `web/src` was hand-rolled: its own overlay, its
 * own backdrop-click handling, and nowhere to hang keyboard or focus
 * behaviour. None had `role="dialog"`, none trapped focus, none closed on
 * Escape. This component owns all of that so a fix reaches every dialog:
 *
 *   - `role="dialog"` + `aria-modal="true"`, labelled by `labelledBy` (the id
 *     of the title element inside) or `label` when there is no title node.
 *   - Focus moves into the dialog on open and returns to whatever was focused
 *     before, on close.
 *   - Tab and Shift+Tab wrap inside the dialog.
 *   - Escape and backdrop click call `onClose`; both can be opted out of,
 *     because a few dialogs deliberately refuse to close over an unsaved form.
 *
 * It deliberately does not impose markup: `overlayClassName` / `className` /
 * `style` default to the existing `.modal-overlay` / `.modal` pair, and each
 * migrated call site passes whatever classes or inline styles it already had,
 * so adoption changed no pixels.
 */

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
    return Array.from(node.querySelectorAll(FOCUSABLE)).filter(
        (el) => !el.hasAttribute('hidden') && el.getAttribute('aria-hidden') !== 'true',
    )
}

export default function Modal({
    open = true,
    onClose,
    labelledBy,
    label,
    className = 'modal',
    overlayClassName = 'modal-overlay',
    style,
    overlayStyle,
    closeOnBackdrop = true,
    closeOnEscape = true,
    initialFocusRef,
    children,
}) {
    const dialogRef = useRef(null)
    const restoreRef = useRef(null)

    // Keep the latest onClose without re-running the key listener effect on
    // every render (call sites pass inline arrow functions).
    const onCloseRef = useRef(onClose)
    onCloseRef.current = onClose

    useEffect(() => {
        if (!open) return undefined
        restoreRef.current = document.activeElement
        const node = dialogRef.current
        if (node) {
            const target = initialFocusRef?.current || focusableIn(node)[0] || node
            target.focus()
        }
        return () => {
            const previous = restoreRef.current
            restoreRef.current = null
            if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
                previous.focus()
            }
        }
        // initialFocusRef is a ref: stable across renders, intentionally not a dep.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open])

    useEffect(() => {
        if (!open) return undefined
        const handleKeyDown = (event) => {
            if (event.key === 'Escape') {
                if (!closeOnEscape) return
                event.stopPropagation()
                onCloseRef.current?.()
                return
            }
            if (event.key !== 'Tab') return
            const node = dialogRef.current
            if (!node) return
            const items = focusableIn(node)
            if (items.length === 0) {
                event.preventDefault()
                node.focus()
                return
            }
            const first = items[0]
            const last = items[items.length - 1]
            const active = document.activeElement
            const outside = !node.contains(active)
            if (event.shiftKey) {
                if (active === first || outside) {
                    event.preventDefault()
                    last.focus()
                }
            } else if (active === last || outside) {
                event.preventDefault()
                first.focus()
            }
        }
        document.addEventListener('keydown', handleKeyDown, true)
        return () => document.removeEventListener('keydown', handleKeyDown, true)
    }, [open, closeOnEscape])

    if (!open) return null

    const handleOverlayClick = (event) => {
        if (!closeOnBackdrop) return
        if (event.target !== event.currentTarget) return
        onCloseRef.current?.()
    }

    return (
        <div className={overlayClassName} style={overlayStyle} onClick={handleOverlayClick}>
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={labelledBy}
                aria-label={labelledBy ? undefined : label}
                tabIndex={-1}
                className={className}
                style={style}
            >
                {children}
            </div>
        </div>
    )
}
