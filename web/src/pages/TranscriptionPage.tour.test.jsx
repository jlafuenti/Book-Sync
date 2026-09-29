import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'
import { roleMeets } from '../roles'
import { TourAnchors, TourScreens, TourRegistryContext } from '../tour/anchors'
import { TourAnchorRegistry } from '../tour/TourAnchorRegistry'

// Issue #598 Track B: the walkthrough's Transcription step needs the tab row
// tagged, and the Queue All button tagged only when it actually renders — the
// same admin gate `TranscriptionPage.roles.test.jsx` already pins (issue #312).

const {
    getPairsMock, getQueueMock, getHistoryMock, addToQueueMock,
    cancelMock, removeMock, priorityMock, statusMock, startMock, authRef,
} = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getQueueMock: vi.fn(),
    getHistoryMock: vi.fn(),
    addToQueueMock: vi.fn(),
    cancelMock: vi.fn(),
    removeMock: vi.fn(),
    priorityMock: vi.fn(),
    statusMock: vi.fn(),
    startMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPairs: getPairsMock,
        getTranscriptionQueue: getQueueMock,
        getQueueHistory: getHistoryMock,
        addToQueue: addToQueueMock,
        cancelTranscription: cancelMock,
        removeFromQueue: removeMock,
        updateQueuePriority: priorityMock,
        getTranscriptionStatus: statusMock,
        startTranscription: startMock,
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
    }),
}))

function pair(overrides = {}) {
    return {
        id: 10,
        status: 'manual_matched',
        ebook: { id: 1, title: 'Dune', author: 'Frank Herbert', format: 'epub' },
        audiobook: { id: 2, title: 'Dune', author: 'Frank Herbert', format: 'm4b' },
        has_transcript: false,
        ...overrides,
    }
}

async function renderPage({ registry } = {}) {
    let tree = <MemoryRouter><TranscriptionPage tab="not-transcribed" /></MemoryRouter>
    if (registry) tree = <TourRegistryContext.Provider value={registry}>{tree}</TourRegistryContext.Provider>
    const utils = render(tree)
    await screen.findAllByText('Dune')
    return utils
}

beforeEach(() => {
    authRef.role = 'admin'
    getPairsMock.mockReset().mockResolvedValue([pair()])
    getQueueMock.mockReset().mockResolvedValue([])
    getHistoryMock.mockReset().mockResolvedValue([])
    addToQueueMock.mockReset().mockResolvedValue({})
    cancelMock.mockReset().mockResolvedValue({})
    removeMock.mockReset().mockResolvedValue({})
    priorityMock.mockReset().mockResolvedValue({})
    statusMock.mockReset().mockResolvedValue({})
    startMock.mockReset().mockResolvedValue({})
})

describe('TranscriptionPage tour anchors and screen readiness (issue #598 Track B)', () => {
    it('tags the tab row for every role', async () => {
        authRef.role = 'user'
        await renderPage()
        expect(document.querySelector('.library-filter-pills')).toHaveAttribute('data-tour', TourAnchors.TranscriptionTabs)
    })

    it('tags Queue All for an admin', async () => {
        authRef.role = 'admin'
        await renderPage()
        expect(document.querySelectorAll(`[data-tour="${TourAnchors.TranscriptionQueueAll}"]`).length).toBeGreaterThan(0)
    })

    it('renders no Queue All (or its anchor) for an editor (issue #312)', async () => {
        authRef.role = 'editor'
        await renderPage()
        expect(document.querySelectorAll(`[data-tour="${TourAnchors.TranscriptionQueueAll}"]`).length).toBe(0)
    })

    it('reports Transcription settled once loaded', async () => {
        const registry = new TourAnchorRegistry()
        await renderPage({ registry })
        await waitFor(() => expect(registry.screenState(TourScreens.Transcription)).toBe('settled'))
    })
})
