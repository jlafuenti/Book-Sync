import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TroubleshootPage from './TroubleshootPage'
import SystemPage from './SystemPage'
import { roleMeets } from '../roles'

/**
 * "Back to System" must not be a dead end (issue #283 follow-up).
 *
 * Troubleshoot and Unsupported are editor-reachable, but the System landing
 * page is admin-only. Both views offered a "Back to System" control that, for an
 * editor, either redirects to the home page or flips to a status view that
 * cannot load — found by clicking it on the deployed branch.
 *
 * Gating a route without gating everything that points at it just moves the
 * dead end somewhere less obvious.
 */

const {
    issuesMock, diskMock, ebooksMock, audiobooksMock, pairsMock,
    queueMock, unsupportedMock, settingsMock, calibreMock, authRef,
} = vi.hoisted(() => ({
    issuesMock: vi.fn(),
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
        getLibraryIssues: issuesMock,
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
    issuesMock.mockReset().mockResolvedValue({ categories: {} })
    diskMock.mockReset().mockResolvedValue({ disks: [] })
    ebooksMock.mockReset().mockResolvedValue([])
    audiobooksMock.mockReset().mockResolvedValue([])
    pairsMock.mockReset().mockResolvedValue([])
    queueMock.mockReset().mockResolvedValue([])
    unsupportedMock.mockReset().mockResolvedValue([])
    settingsMock.mockReset().mockResolvedValue({})
    calibreMock.mockReset().mockResolvedValue({ available: false })
})

describe('Troubleshoot — Back to System', () => {
    it('is hidden from an editor, who cannot open /system/status', async () => {
        authRef.role = 'editor'
        render(<MemoryRouter><TroubleshootPage /></MemoryRouter>)
        await waitFor(() => expect(issuesMock).toHaveBeenCalled())
        expect(screen.queryByText('Back to System')).toBeNull()
    })

    it('is offered to an admin', async () => {
        authRef.role = 'admin'
        render(<MemoryRouter><TroubleshootPage /></MemoryRouter>)
        await waitFor(() => expect(issuesMock).toHaveBeenCalled())
        expect(screen.getByText('Back to System')).toBeInTheDocument()
    })
})

describe('Unsupported files — Back to System', () => {
    it('is hidden from an editor, whose status view would not load', async () => {
        authRef.role = 'editor'
        render(<MemoryRouter><SystemPage tab="unsupported" /></MemoryRouter>)
        await waitFor(() => expect(unsupportedMock).toHaveBeenCalled())
        expect(screen.queryByText('Back to System')).toBeNull()
    })

    it('is offered to an admin', async () => {
        authRef.role = 'admin'
        render(<MemoryRouter><SystemPage tab="unsupported" /></MemoryRouter>)
        await waitFor(() => expect(unsupportedMock).toHaveBeenCalled())
        expect(screen.getByText('Back to System')).toBeInTheDocument()
    })
})
