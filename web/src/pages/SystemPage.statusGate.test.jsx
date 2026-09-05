import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SystemPage from './SystemPage'
import { roleMeets } from '../roles'

/**
 * Issue #283: the status view's reads are admin-only server-side now.
 *
 * The route guard in App.jsx already keeps non-admins off `/system/status`, so
 * this is the second layer — but it earns its place: `/system/unsupported`
 * renders this same component and *is* reachable by editors, so a change to
 * which tab triggers `loadStatus()` would start firing admin-only reads from an
 * editor's session. The failure would surface as "Failed to load system status"
 * on a page the editor is entitled to use, which reads as a broken app rather
 * than as a permission boundary.
 */

const {
    diskMock, ebooksMock, audiobooksMock, pairsMock, queueMock,
    unsupportedMock, settingsMock, calibreMock, authRef,
} = vi.hoisted(() => ({
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

    it('leaves the unsupported tab working for an editor', async () => {
        // GET /api/library/unsupported is editor-level on purpose: it drives
        // conversion, which is library maintenance rather than infrastructure.
        authRef.role = 'editor'
        renderPage('unsupported')
        await waitFor(() => expect(unsupportedMock).toHaveBeenCalled())
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

    it('does not fire from the one tab an editor can open', async () => {
        authRef.role = 'editor'
        renderPage('unsupported')
        await waitFor(() => expect(unsupportedMock).toHaveBeenCalled())
        expect(calibreMock).not.toHaveBeenCalled()
    })
})
