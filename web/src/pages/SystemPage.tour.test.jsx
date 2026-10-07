import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SystemPage from './SystemPage'
import { roleMeets } from '../roles'
import { TourAnchors, TourScreens, TourRegistryContext } from '../tour/anchors'
import { TourAnchorRegistry } from '../tour/TourAnchorRegistry'

// Issue #598 Track B: the walkthrough's System step needs five real anchors
// (Status, Troubleshoot card, Transcription settings, User Management,
// Backups — the last two admin-only, matching the sections they wrap) and a
// settled report tied to the same statusLoading gate the dashboard itself
// already waits on.

const {
    diskMock, ebooksMock, audiobooksMock, pairsMock, queueMock,
    unsupportedMock, settingsMock, calibreMock, getUsersMock, getUpdateStatusMock,
    getBackupStatusMock, listBackupsMock, authRef,
} = vi.hoisted(() => ({
    diskMock: vi.fn(),
    ebooksMock: vi.fn(),
    audiobooksMock: vi.fn(),
    pairsMock: vi.fn(),
    queueMock: vi.fn(),
    unsupportedMock: vi.fn(),
    settingsMock: vi.fn(),
    calibreMock: vi.fn(),
    getUsersMock: vi.fn(),
    getUpdateStatusMock: vi.fn(),
    getBackupStatusMock: vi.fn(),
    listBackupsMock: vi.fn(),
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
        getSyncMapRebuildStatus: vi.fn().mockResolvedValue({ outdated: 0, total: 0, running: false, results: [] }),
        getWordTimingStatus: vi.fn().mockResolvedValue({ with_words: 0, without_words: 0, queued: 0 }),
        getUsers: getUsersMock,
        getUpdateStatus: getUpdateStatusMock,
        getBackupStatus: getBackupStatusMock,
        listBackups: listBackupsMock,
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
    }),
}))

// The two panels this page pulls in are exercised by their own test suites;
// stub them here so a missing/renamed prop there cannot fail this file.
vi.mock('./UserManagementPage', () => ({ UserManagementSection: () => <div>user-management-stub</div> }))
vi.mock('../components/UpdateCheckBanner', () => ({ default: () => null }))
vi.mock('../components/UpdateCheckSettings', () => ({ default: () => null }))
vi.mock('../components/GoogleBooksSettings', () => ({ default: () => null }))

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
    getUsersMock.mockReset().mockResolvedValue([])
    getUpdateStatusMock.mockReset().mockResolvedValue({})
    getBackupStatusMock.mockReset().mockResolvedValue({})
    listBackupsMock.mockReset().mockResolvedValue({ items: [] })
})

function renderPage(tab, { registry } = {}) {
    let tree = <MemoryRouter><SystemPage tab={tab} /></MemoryRouter>
    if (registry) tree = <TourRegistryContext.Provider value={registry}>{tree}</TourRegistryContext.Provider>
    return render(tree)
}

describe('SystemPage tour anchors and screen readiness (issue #598 Track B)', () => {
    it('tags System Status, the Troubleshoot card and Transcription settings for an admin', async () => {
        renderPage('status')
        await screen.findByText('System Status')

        expect(document.querySelector(`[data-tour="${TourAnchors.SystemStatus}"]`)).toHaveTextContent('System Status')
        const troubleshootCard = document.querySelector(`[data-tour="${TourAnchors.SystemTroubleshootCard}"]`)
        expect(troubleshootCard).toHaveTextContent('Troubleshoot Library')
        expect(document.querySelector(`[data-tour="${TourAnchors.SystemTranscriptionSettings}"]`)).toHaveTextContent('Transcription Settings')
    })

    it('tags User Management and Backups for an admin', async () => {
        renderPage('status')
        await screen.findByText('System Status')

        expect(document.querySelector(`[data-tour="${TourAnchors.SystemUserManagement}"]`)).toHaveTextContent('User Management')
        expect(document.querySelector(`[data-tour="${TourAnchors.SystemBackups}"]`)).toHaveTextContent('Backups')
    })

    it('reports System settled once the status dashboard has loaded', async () => {
        const registry = new TourAnchorRegistry()
        renderPage('status', { registry })

        expect(registry.screenState(TourScreens.System)).toBe('loading')
        await waitFor(() => expect(registry.screenState(TourScreens.System)).toBe('settled'))
    })
})
