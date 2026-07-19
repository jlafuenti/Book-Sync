import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TroubleshootPage from './TroubleshootPage'

const {
    getLibraryIssuesMock, repairChapterEncodingMock, bulkRepairChapterEncodingMock,
    getLibraryScanProgressMock,
} = vi.hoisted(() => ({
    getLibraryIssuesMock: vi.fn(),
    repairChapterEncodingMock: vi.fn(),
    bulkRepairChapterEncodingMock: vi.fn(),
    getLibraryScanProgressMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getLibraryIssues: getLibraryIssuesMock,
    startLibraryScan: vi.fn(),
    getLibraryScanProgress: getLibraryScanProgressMock,
    cancelLibraryScan: vi.fn(),
    bulkDeleteIssues: vi.fn(),
    replaceLibraryFile: vi.fn(),
    requeuePair: vi.fn(),
    dismissFailedAcsm: vi.fn(),
    deleteEbook: vi.fn(),
    deleteAudiobook: vi.fn(),
    convertUnsupportedFile: vi.fn(),
    rescanBook: vi.fn(),
    deleteOrphanCovers: vi.fn(),
    getEbook: vi.fn(),
    getAudiobook: vi.fn(),
    updateEbookMetadata: vi.fn(),
    updateAudiobookMetadata: vi.fn(),
    repairChapterEncoding: repairChapterEncodingMock,
    bulkRepairChapterEncoding: bulkRepairChapterEncodingMock,
}))

vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ hasMinRole: () => true }) }))
vi.mock('../components/EnhancedMetadataModal', () => ({ default: () => null }))

function issuesWithChapterEncodingBad(rows, extra = {}) {
    const categories = {
        missing: [], zero_byte: [], chapter_encoding_bad: rows, audio_corrupt: [],
        ebook_drm: [], ebook_unreadable: [], unsupported_format: [], sync_map_missing: [],
        duplicate: [], missing_cover: [], orphaned_cover: [], failed_transcription: [], failed_acsm: [],
        ...extra,
    }
    return { categories, counts: Object.fromEntries(Object.entries(categories).map(([k, v]) => [k, v.length])), total: rows.length + Object.values(extra).reduce((n, v) => n + v.length, 0) }
}

function renderPage() {
    return render(<MemoryRouter><TroubleshootPage /></MemoryRouter>)
}

beforeEach(() => {
    getLibraryIssuesMock.mockReset()
    repairChapterEncodingMock.mockReset()
    bulkRepairChapterEncodingMock.mockReset()
    getLibraryScanProgressMock.mockReset().mockResolvedValue({ running: false })
})

describe('TroubleshootPage chapter encoding repair', () => {
    it('lists a bad-chapter-encoding audiobook and repairs it via the row action', async () => {
        const row = { item_type: 'audiobook', item_id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr', detail: "chapter 0 title: invalid continuation byte", file_size: 100 }
        getLibraryIssuesMock.mockResolvedValueOnce(issuesWithChapterEncodingBad([row]))
        getLibraryIssuesMock.mockResolvedValueOnce(issuesWithChapterEncodingBad([]))
        repairChapterEncodingMock.mockResolvedValue({ status: 'repaired', detail: null, item_id: 1538 })
        renderPage()

        fireEvent.click(await screen.findByText(/Audiobooks with corrupt chapter titles/))
        fireEvent.click(await screen.findByRole('button', { name: 'Repair' }))

        await waitFor(() => expect(repairChapterEncodingMock).toHaveBeenCalledWith(1538))
        await waitFor(() => expect(getLibraryIssuesMock).toHaveBeenCalledTimes(2))
    })

    it('bulk-repairs selected rows and shows a warning listing failures', async () => {
        const rows = [
            { item_type: 'audiobook', item_id: 1, title: 'Book One', detail: 'bad', file_size: 100 },
            { item_type: 'audiobook', item_id: 2, title: 'Book Two', detail: 'bad', file_size: 100 },
        ]
        getLibraryIssuesMock.mockResolvedValue(issuesWithChapterEncodingBad(rows))
        bulkRepairChapterEncodingMock.mockResolvedValue({
            repaired: 1,
            failures: [{ item_id: 2, title: 'Book Two', error: 'ffmpeg not found' }],
        })
        renderPage()

        fireEvent.click(await screen.findByText(/Audiobooks with corrupt chapter titles/))
        const checkboxes = await screen.findAllByRole('checkbox')
        // First checkbox is "select all"; tick both row checkboxes.
        fireEvent.click(checkboxes[1])
        fireEvent.click(checkboxes[2])

        fireEvent.click(await screen.findByRole('button', { name: 'Repair Selected' }))

        await waitFor(() => expect(bulkRepairChapterEncodingMock).toHaveBeenCalledWith([1, 2]))
        const warning = await screen.findByText(/ffmpeg not found/)
        expect(warning.textContent).toContain('Book Two')
        expect(warning.closest('.alert')).toHaveClass('alert-warning')
    })

    it('shows a plain success message when bulk repair has no failures', async () => {
        const rows = [
            { item_type: 'audiobook', item_id: 1, title: 'Book One', detail: 'bad', file_size: 100 },
        ]
        getLibraryIssuesMock.mockResolvedValue(issuesWithChapterEncodingBad(rows))
        bulkRepairChapterEncodingMock.mockResolvedValue({ repaired: 1, failures: [] })
        renderPage()

        fireEvent.click(await screen.findByText(/Audiobooks with corrupt chapter titles/))
        const checkboxes = await screen.findAllByRole('checkbox')
        fireEvent.click(checkboxes[1])

        fireEvent.click(await screen.findByRole('button', { name: 'Repair Selected' }))

        await waitFor(() => expect(bulkRepairChapterEncodingMock).toHaveBeenCalledWith([1]))
        const success = await screen.findByText('Repaired 1')
        expect(success.closest('.alert')).toHaveClass('alert-success')
    })

    it('shows "Delete Selected" (not "Repair Selected") for a non-repair selectable category', async () => {
        const orphanRow = { filename: 'orphan.jpg', file_path: '/covers/orphan.jpg', file_size: 100, detail: 'Cover file not referenced by any book' }
        getLibraryIssuesMock.mockResolvedValue(issuesWithChapterEncodingBad([], { orphaned_cover: [orphanRow] }))
        renderPage()

        fireEvent.click(await screen.findByText(/Orphaned cover files/))
        const checkboxes = await screen.findAllByRole('checkbox')
        fireEvent.click(checkboxes[1])

        expect(await screen.findByRole('button', { name: 'Delete Selected' })).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Repair Selected' })).not.toBeInTheDocument()
    })
})
