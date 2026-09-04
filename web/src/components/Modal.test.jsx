import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import Modal from './Modal'

// Issue #279: every modal in the app was hand-rolled — no dialog role, no
// focus trap, no Escape. These tests pin the shared primitive's contract.

function Harness({ closeOnBackdrop, closeOnEscape, initialOpen = false }) {
    const [open, setOpen] = useState(initialOpen)
    return (
        <div>
            <button onClick={() => setOpen(true)}>Open</button>
            <Modal
                open={open}
                onClose={() => setOpen(false)}
                labelledBy="harness-title"
                closeOnBackdrop={closeOnBackdrop}
                closeOnEscape={closeOnEscape}
            >
                <h3 id="harness-title">Harness Dialog</h3>
                <button>First</button>
                <button>Last</button>
            </Modal>
        </div>
    )
}

describe('Modal primitive (issue #279)', () => {
    it('renders nothing while closed', () => {
        render(<Harness />)
        expect(screen.queryByRole('dialog')).toBeNull()
    })

    it('exposes role="dialog", aria-modal and aria-labelledby', () => {
        render(
            <Modal onClose={vi.fn()} labelledBy="t">
                <h3 id="t">Confirm Delete</h3>
            </Modal>,
        )
        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAttribute('aria-labelledby', 't')
        expect(screen.getByText('Confirm Delete')).toBeInTheDocument()
    })

    it('falls back to aria-label when there is no title element', () => {
        render(<Modal onClose={vi.fn()} label="Bulk match"><p>Body</p></Modal>)
        expect(screen.getByRole('dialog')).toHaveAttribute('aria-label', 'Bulk match')
    })

    it('moves focus into the dialog on open and restores it to the trigger on close', () => {
        render(<Harness />)
        const trigger = screen.getByRole('button', { name: 'Open' })
        trigger.focus()
        fireEvent.click(trigger)

        const dialog = screen.getByRole('dialog')
        expect(dialog.contains(document.activeElement)).toBe(true)

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(screen.queryByRole('dialog')).toBeNull()
        expect(document.activeElement).toBe(trigger)
    })

    it('focuses the dialog itself when it holds nothing focusable', () => {
        render(<Modal onClose={vi.fn()} label="Empty"><p>Nothing here</p></Modal>)
        expect(document.activeElement).toBe(screen.getByRole('dialog'))
    })

    it('closes on Escape', () => {
        const onClose = vi.fn()
        render(<Modal onClose={onClose} label="X"><button>ok</button></Modal>)
        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onClose).toHaveBeenCalledTimes(1)
    })

    it('ignores Escape when closeOnEscape is false', () => {
        const onClose = vi.fn()
        render(<Modal onClose={onClose} label="X" closeOnEscape={false}><button>ok</button></Modal>)
        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onClose).not.toHaveBeenCalled()
    })

    it('closes on backdrop click but not on content click', () => {
        const onClose = vi.fn()
        const { container } = render(
            <Modal onClose={onClose} label="X"><button>Inside</button></Modal>,
        )
        fireEvent.click(screen.getByRole('button', { name: 'Inside' }))
        expect(onClose).not.toHaveBeenCalled()

        fireEvent.click(container.querySelector('.modal-overlay'))
        expect(onClose).toHaveBeenCalledTimes(1)
    })

    it('ignores backdrop clicks when closeOnBackdrop is false', () => {
        const onClose = vi.fn()
        const { container } = render(
            <Modal onClose={onClose} label="X" closeOnBackdrop={false}><button>Inside</button></Modal>,
        )
        fireEvent.click(container.querySelector('.modal-overlay'))
        expect(onClose).not.toHaveBeenCalled()
    })

    it('traps Tab inside the dialog, wrapping in both directions', () => {
        render(<Harness initialOpen />)
        const [first, last] = screen.getAllByRole('button').filter(b => b.textContent !== 'Open')

        last.focus()
        fireEvent.keyDown(document, { key: 'Tab' })
        expect(document.activeElement).toBe(first)

        first.focus()
        fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
        expect(document.activeElement).toBe(last)
    })

    it('leaves Tab alone in the middle of the dialog', () => {
        render(<Harness initialOpen />)
        const first = screen.getByRole('button', { name: 'First' })
        first.focus()
        fireEvent.keyDown(document, { key: 'Tab' })
        // Not wrapped: the browser's own tab order takes over.
        expect(document.activeElement).toBe(first)
    })

    it('applies the class and style overrides so migrated modals keep their look', () => {
        const { container } = render(
            <Modal
                onClose={vi.fn()}
                label="X"
                overlayClassName="import-modal-overlay"
                className="import-modal"
                style={{ maxWidth: '860px' }}
            >
                <p>Body</p>
            </Modal>,
        )
        expect(container.querySelector('.import-modal-overlay')).toBeTruthy()
        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveClass('import-modal')
        expect(dialog.style.maxWidth).toBe('860px')
    })
})
