import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SystemPage from './SystemPage'
import { roleMeets } from '../roles'

/**
 * Issue #283: the status view's reads are admin-only server-side now.
 *
 * The route guard in App.jsx already keeps non-admins off `/system/status`, so
 * this is the second layer. (`/system/unsupported` used to render this same
 * component for editors; it now redirects to Troubleshoot, where the
 * unsupported-files panel lives.)
 */

const {
    diskMock, ebooksMock, audiobooksMock, pairsMock, queueMock,
    unsupportedMock, settingsMock, calibreMock, rebuildStatusMock, wordTimingMock, authRef,
} = vi.hoisted(() => ({
    rebuildStatusMock: vi.fn(),
    wordTimingMock: vi.fn(),
    diskMock: vi.fn(),
    ebooksMock: vi.fn(),
    audiobooksMock: vi.fn(),
    pairsMock: vi.fn(),
    queueMock: vi.fn(),
    unsupportedMock: vi.fn(),
    settingsMock: vi.fn(),
    calibreMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getDiskUsage: diskMock,
        getEbooks: ebooksMock,
        getAudiobooks: audiobooksMock,
        getPairs: pairsMock,
        getTranscriptionQueue: queueMock,
        getUnsupportedFiles: unsupportedMock,
        getSettings: settingsMock,
        getCalibreStatus: calibreMock,
        getSyncMapRebuildStatus: rebuildStatusMock,
        getWordTimingStatus: wordTimingMock,
    }
})

// The real comparison, not a copy of it: these mocks used to reimplement
// `(ROLE_HIERARCHY[role] || 0) >= (ROLE_HIERARCHY[min] || 0)`, which is the
// fail-open form, so a typo'd minimum behaved the same here as in the app
// and the suite stayed green either way (issue #359).
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
    }),
}))

beforeEach(() => {
    authRef.role = 'admin'
    diskMock.mockReset().mockResolvedValue({ disks: [] })
    ebooksMock.mockReset().mockResolvedValue([])
    audiobooksMock.mockReset().mockResolvedValue([])
    pairsMock.mockReset().mockResolvedValue([])
    queueMock.mockReset().mockResolvedValue([])
    unsupportedMock.mockReset().mockResolvedValue([])
    settingsMock.mockReset().mockResolvedValue({})
    calibreMock.mockReset().mockResolvedValue({ available: false })
    rebuildStatusMock.mockReset().mockResolvedValue({ outdated: 0, total: 0, running: false, results: [] })
    wordTimingMock.mockReset().mockResolvedValue({ with_words: 4, without_words: 0, queued: 0 })
})

function renderPage(tab) {
    return render(<MemoryRouter><SystemPage tab={tab} /></MemoryRouter>)
}

describe('the status reads are admin-only', () => {
    it('fires them for an admin', async () => {
        authRef.role = 'admin'
        renderPage('status')
        await waitFor(() => expect(diskMock).toHaveBeenCalled())
    })

    it('does not fire them for an editor', async () => {
        authRef.role = 'editor'
        renderPage('status')
        // Nothing to await on a path that should do nothing, so let any pending
        // effect flush and then assert it stayed quiet.
        await new Promise(r => setTimeout(r, 0))
        expect(diskMock).not.toHaveBeenCalled()
    })

    it('does not fire them for a plain user', async () => {
        authRef.role = 'user'
        renderPage('status')
        await new Promise(r => setTimeout(r, 0))
        expect(diskMock).not.toHaveBeenCalled()
    })

})

/**
 * Issue #208: `GET /api/library/calibre-status` is editor-and-up server-side --
 * it shells out to `ebook-convert --version`, and a read-only account can
 * convert nothing.
 *
 * `CalibreStatusCard` is the only caller in the whole web client
 * (`getCalibreStatus` has one import site, here), and it lives inside the
 * status dashboard, which never renders below admin: `loadStatus` is the only
 * thing that clears `statusLoading`, and it is `canAdmin`-gated. So the tile
 * cannot fire from a session that would be refused. Pinned because the coupling
 * is indirect -- someone moving the card out of the dashboard, or giving the
 * page a non-admin loading path, would silently start firing a read that 403s.
 */
describe('the calibre probe follows the same gate', () => {
    it('fires for an admin', async () => {
        authRef.role = 'admin'
        renderPage('status')
        await waitFor(() => expect(calibreMock).toHaveBeenCalled())
    })

    it('does not fire for an editor', async () => {
        authRef.role = 'editor'
        renderPage('status')
        await new Promise(r => setTimeout(r, 0))
        expect(calibreMock).not.toHaveBeenCalled()
    })

    it('does not fire for a plain user', async () => {
        // Belt and braces: RequireRole keeps a plain user off every /system
        // route (App.test.jsx), so this component never mounts for them at all.
        authRef.role = 'user'
        renderPage('status')
        await new Promise(r => setTimeout(r, 0))
        expect(calibreMock).not.toHaveBeenCalled()
    })

})

/**
 * Issue #774: the sync-map rebuild card calls an admin-only endpoint, so it is
 * rendered for admins and never mounted for anyone else. An editor never gets
 * past the status gate above, and the role check on the card itself is the
 * second layer.
 */
describe('the word-timing card is admin-only (issue #835)', () => {
    it('appears for an admin', async () => {
        authRef.role = 'admin'
        renderPage('status')
        expect(await screen.findByText('Word timing')).toBeInTheDocument()
        expect(wordTimingMock).toHaveBeenCalled()
    })

    it('does not appear, or fetch, for an editor', async () => {
        authRef.role = 'editor'
        renderPage('status')
        await new Promise(r => setTimeout(r, 0))
        expect(screen.queryByText('Word timing')).not.toBeInTheDocument()
        expect(wordTimingMock).not.toHaveBeenCalled()
    })
})

// The sync-map rebuild card moved to Troubleshoot Library (covered in
// TroubleshootPage.test.jsx); the System page no longer mounts or fetches it.
describe('the sync-map rebuild card has left the System page', () => {
    it('is not shown, and its status is not fetched', async () => {
        authRef.role = 'admin'
        renderPage('status')
        await screen.findByText('Word timing')
        expect(screen.queryByText('Rebuild Sync Maps')).not.toBeInTheDocument()
        expect(rebuildStatusMock).not.toHaveBeenCalled()
    })
})
