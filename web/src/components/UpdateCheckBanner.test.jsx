import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import UpdateCheckBanner from './UpdateCheckBanner'

/**
 * What the System page says about releases (issue #463).
 *
 * The check is opt-in — when enabled the server contacts api.github.com, and
 * docs/privacy.md promises no outbound call nobody asked for — so the first
 * thing an admin sees is a question, not a banner. After that the page is
 * quiet unless there is something to act on.
 */

const base = {
    enabled: true,
    prompted: true,
    status: 'unknown',
    reason: null,
    running_version: '0.1.0',
    latest_version: null,
    release_url: null,
    checked_at: null,
}

const renderBanner = (status, handlers = {}) =>
    render(
        <UpdateCheckBanner
            status={status}
            onEnable={handlers.onEnable ?? vi.fn()}
            onDecline={handlers.onDecline ?? vi.fn()}
        />,
    )

describe('before the admin has answered', () => {
    const unasked = { ...base, enabled: false, prompted: false, reason: 'disabled' }

    it('asks, rather than checking silently', () => {
        renderBanner(unasked)
        expect(screen.getByText(/check for updates automatically/i)).toBeInTheDocument()
    })

    it('says what enabling it will do, so the answer is informed', () => {
        renderBanner(unasked)
        expect(screen.getByText(/github/i)).toBeInTheDocument()
    })

    it('Enable and No thanks each report the answer', () => {
        const onEnable = vi.fn()
        const onDecline = vi.fn()
        renderBanner(unasked, { onEnable, onDecline })

        fireEvent.click(screen.getByRole('button', { name: /enable/i }))
        expect(onEnable).toHaveBeenCalledTimes(1)

        fireEvent.click(screen.getByRole('button', { name: /no thanks/i }))
        expect(onDecline).toHaveBeenCalledTimes(1)
    })
})

describe('once answered', () => {
    it('says nothing when the admin declined', () => {
        const { container } = renderBanner({ ...base, enabled: false, prompted: true, reason: 'disabled' })
        expect(container).toBeEmptyDOMElement()
    })

    it('names both versions when a newer release is out', () => {
        renderBanner({
            ...base,
            status: 'available',
            latest_version: '0.2.0',
            release_url: 'https://github.com/jlafuenti/Book-Sync/releases/tag/v0.2.0',
        })

        const banner = screen.getByRole('status')
        expect(banner).toHaveTextContent('0.2.0')
        expect(banner).toHaveTextContent('0.1.0')
    })

    it('tells the operator how to update, since there is deliberately no button for it', () => {
        // Updating from inside the app would need the Docker socket, undoing the
        // container hardening of issue #180. The page shows the steps instead.
        renderBanner({ ...base, status: 'available', latest_version: '0.2.0' })
        expect(screen.getByRole('status')).toHaveTextContent('docker compose up -d --build')
        expect(screen.getByRole('status')).toHaveTextContent('v0.2.0')
    })

    it('links the release notes', () => {
        renderBanner({
            ...base,
            status: 'available',
            latest_version: '0.2.0',
            release_url: 'https://github.com/jlafuenti/Book-Sync/releases/tag/v0.2.0',
        })

        const link = screen.getByRole('link', { name: /release notes/i })
        expect(link).toHaveAttribute('href', 'https://github.com/jlafuenti/Book-Sync/releases/tag/v0.2.0')
        expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
    })

    it.each([
        'javascript:alert(document.cookie)',
        'https://evil.example/releases/tag/v0.2.0',
        'http://github.com/jlafuenti/Book-Sync/releases/tag/v0.2.0',
    ])('never renders a link it did not expect (%s)', (url) => {
        // The URL reaches the page from GitHub's API through the server. Refresh
        // tokens live in localStorage (CLAUDE.md, issue #286), so a `javascript:`
        // href is account takeover, not a broken link. Only the project's own
        // release pages are linked.
        renderBanner({ ...base, status: 'available', latest_version: '0.2.0', release_url: url })

        expect(screen.queryByRole('link')).toBeNull()
        expect(screen.getByRole('status')).toHaveTextContent('0.2.0')
    })

    it('is quiet when up to date', () => {
        renderBanner({ ...base, status: 'current' })

        expect(screen.queryByRole('status')).toBeNull()
        expect(screen.getByText(/up to date/i)).toHaveTextContent('0.1.0')
    })

    it('says plainly when no release has been published yet', () => {
        renderBanner({ ...base, status: 'unknown', reason: 'no_releases' })
        expect(screen.getByText(/no releases/i)).toBeInTheDocument()
    })

    it('says so when GitHub could not be asked', () => {
        renderBanner({ ...base, status: 'unknown', reason: 'unreachable' })
        expect(screen.getByText(/couldn.t check for updates/i)).toBeInTheDocument()
    })

    it('renders nothing before the status has loaded', () => {
        const { container } = renderBanner(null)
        expect(container).toBeEmptyDOMElement()
    })
})

/**
 * The transcription worker's version. The server asks its configured worker
 * for `worker_version` and compares it with its own; a worker left behind
 * after a server upgrade is the failure nothing else names. This line renders
 * whether or not the GitHub check is enabled — it is the operator's own
 * machine, not a third party.
 */
const worker = (overrides) => ({
    configured: true,
    status: 'current',
    reason: null,
    version: '0.1.0',
    server_version: '0.1.0',
    checked_at: '2026-09-13T00:00:00Z',
    ...overrides,
})

describe('the transcription worker', () => {
    it('says when the worker is behind the server, and how to update it', () => {
        renderBanner({ ...base, status: 'current', worker: worker({ status: 'behind', version: '0.1.0', server_version: '0.2.0' }) })
        const alert = screen.getByRole('status', { name: /transcription worker/i })
        expect(alert).toHaveTextContent(/worker is on 0\.1\.0/)
        expect(alert).toHaveTextContent(/this server is 0\.2\.0/)
        expect(alert).toHaveTextContent(/docker compose up -d --build/)
    })

    it('is quiet when the worker matches', () => {
        renderBanner({ ...base, status: 'current', worker: worker() })
        expect(screen.getByText(/Transcription worker up to date · 0\.1\.0/)).toBeInTheDocument()
        expect(screen.queryByRole('status', { name: /transcription worker/i })).not.toBeInTheDocument()
    })

    it('says nothing about a worker when none is configured', () => {
        renderBanner({ ...base, status: 'current', worker: worker({ configured: false, status: 'unknown', reason: 'not_configured', version: null }) })
        expect(screen.queryByText(/transcription worker/i)).not.toBeInTheDocument()
    })

    it('says when the worker could not be reached, without alarm', () => {
        renderBanner({ ...base, status: 'current', worker: worker({ status: 'unknown', reason: 'unreachable', version: null }) })
        expect(screen.getByText(/Couldn.t reach the transcription worker/)).toBeInTheDocument()
    })

    it('does not call an old worker that reports no version current', () => {
        renderBanner({ ...base, status: 'current', worker: worker({ status: 'unknown', reason: 'unreported', version: null }) })
        expect(screen.getByText(/does not report a version/)).toBeInTheDocument()
        expect(screen.queryByText(/up to date · /)).not.toBeInTheDocument()
    })

    it('says when the worker rejected the key', () => {
        renderBanner({ ...base, status: 'current', worker: worker({ status: 'unknown', reason: 'unauthorized', version: null }) })
        expect(screen.getByText(/rejected this server.s key/)).toBeInTheDocument()
    })

    it('still reports the worker when the GitHub check was declined', () => {
        renderBanner({ ...base, enabled: false, prompted: true, worker: worker({ status: 'behind', version: '0.1.0', server_version: '0.2.0' }) })
        expect(screen.getByRole('status', { name: /transcription worker/i })).toBeInTheDocument()
    })

    it('renders the release banner and the worker line together', () => {
        renderBanner({ ...base, status: 'available', latest_version: '0.3.0', worker: worker({ status: 'behind', version: '0.1.0', server_version: '0.2.0' }) })
        expect(screen.getByText(/Tandem 0\.3\.0 is available/)).toBeInTheDocument()
        expect(screen.getByRole('status', { name: /transcription worker/i })).toBeInTheDocument()
    })

    it('tolerates a status without a worker field', () => {
        renderBanner({ ...base, status: 'current' })
        expect(screen.getByText(/Up to date · Tandem 0\.1\.0/)).toBeInTheDocument()
    })
})
