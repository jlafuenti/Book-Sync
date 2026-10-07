import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SystemPage from './SystemPage'
import { roleMeets } from '../roles'

/**
 * The System page's layout after the clean-up: library maintenance (unsupported
 * files, sync-map rebuilds) lives in Troubleshoot Library, related settings sit
 * side by side, and the Troubleshoot entry says how much is open before you
 * click in.
 */

const {
    diskMock, ebooksMock, audiobooksMock, pairsMock, queueMock, unsupportedMock,
    settingsMock, calibreMock, rebuildStatusMock, wordTimingMock, issuesMock,
} = vi.hoisted(() => ({
    diskMock: vi.fn(),
    ebooksMock: vi.fn(),
    audiobooksMock: vi.fn(),
    pairsMock: vi.fn(),
    queueMock: vi.fn(),
    unsupportedMock: vi.fn(),
    settingsMock: vi.fn(),
    calibreMock: vi.fn(),
    rebuildStatusMock: vi.fn(),
    wordTimingMock: vi.fn(),
    issuesMock: vi.fn(),
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
        getLibraryIssues: issuesMock,
        getUsers: vi.fn().mockResolvedValue([]),
        getUpdateStatus: vi.fn().mockResolvedValue(null),
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ hasMinRole: (min) => roleMeets('admin', min) }),
}))

function issues(categories) {
    const total = Object.values(categories).reduce((n, rows) => n + rows.length, 0)
    return { categories, counts: {}, total }
}

beforeEach(() => {
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
    issuesMock.mockReset().mockResolvedValue(issues({}))
})

async function renderPage() {
    render(<MemoryRouter><SystemPage tab="status" /></MemoryRouter>)
    await screen.findByText('Troubleshoot Library')
}

const pairOf = (title) => screen.getByText(title, { selector: 'h3' }).closest('.system-two-col')

describe('library maintenance has moved to Troubleshoot', () => {
    it('shows no Unsupported Files card and does not fetch the list', async () => {
        await renderPage()

        expect(screen.queryByText('Unsupported Files')).toBeNull()
        expect(unsupportedMock).not.toHaveBeenCalled()
    })

    it('shows no Rebuild Sync Maps card', async () => {
        await renderPage()

        expect(screen.queryByText('Rebuild Sync Maps')).toBeNull()
    })
})

describe('import sources', () => {
    it('lists only the services that work', async () => {
        await renderPage()

        expect(screen.getByText('Pull books from Audible and Google Play.')).toBeInTheDocument()
        expect(screen.queryByText(/Nook/)).toBeNull()
    })
})

describe('configuration pairs', () => {
    it('puts Hardcover beside Audiobookshelf', async () => {
        await renderPage()

        const row = pairOf('Audiobookshelf Integration')
        expect(row).not.toBeNull()
        expect(row).toBe(pairOf('Hardcover Integration'))
    })

    it('puts Updates beside the detailed disk breakdown', async () => {
        await renderPage()

        const row = pairOf('Detailed Disk Breakdown')
        expect(row).not.toBeNull()
        expect(row).toBe(pairOf('Updates'))
    })

    it('leaves Google Books on its own', async () => {
        await renderPage()

        expect(pairOf('Google Books')).toBeNull()
    })
})

describe('troubleshoot status badge', () => {
    it('counts open items, leaving out those already queued for a fix', async () => {
        issuesMock.mockResolvedValue(issues({
            transcript_out_of_step: [{ pair_id: 1 }, { pair_id: 2 }, { pair_id: 3 }],
            missing_cover: [{ item_id: 9 }, { item_id: 10 }],
        }))
        queueMock.mockResolvedValue([
            { book_pair_id: 1, status: 'pending' }, { book_pair_id: 2, status: 'in_progress' },
        ])
        await renderPage()

        const badge = await screen.findByText('3 open')
        expect(badge.getAttribute('title')).toMatch(/2 more already queued/)
    })

    it('says so when nothing is open', async () => {
        issuesMock.mockResolvedValue(issues({ transcript_out_of_step: [{ pair_id: 1 }] }))
        queueMock.mockResolvedValue([{ book_pair_id: 1, status: 'pending' }])
        await renderPage()

        expect(await screen.findByText('No open issues')).toBeInTheDocument()
    })

    it('shows no badge, and keeps the dashboard, when the issue list fails to load', async () => {
        issuesMock.mockRejectedValue(new Error('boom'))
        await renderPage()

        await waitFor(() => expect(issuesMock).toHaveBeenCalled())
        expect(screen.queryByText(/ open$/)).toBeNull()
        expect(screen.queryByText('No open issues')).toBeNull()
        expect(screen.queryByText('Failed to load system status')).toBeNull()
    })
})
