import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import ImportSourcesPage from './ImportSourcesPage'

// Issue #216: `last_sync_at` is naive UTC. Parsed as local time it lands in
// the FUTURE for any zone west of UTC, so a sync from this morning read
// "Just now" for as many hours as the offset. The relative label must be
// measured from the UTC instant the server meant.

const { listSourcesMock, getJobsMock } = vi.hoisted(() => ({
    listSourcesMock: vi.fn(),
    getJobsMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, listImportSources: listSourcesMock, getImportJobs: getJobsMock }
})

const source = (overrides = {}) => ({
    source_key: 'audible',
    display_name: 'Audible',
    connected: true,
    enabled: true,
    last_status: 'success',
    last_sync_at: null,
    supports_auto_sync: true,
    auto_sync_enabled: false,
    cadence_hours: 24,
    progress: null,
    ...overrides,
})

// 2026-08-22 14:03 UTC — the browser sits at whatever zone the runner is in.
const NOW = Date.UTC(2026, 7, 22, 14, 3, 0)

beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    listSourcesMock.mockReset()
    getJobsMock.mockReset().mockResolvedValue([])
})
afterEach(() => vi.restoreAllMocks())

describe('ImportSourcesPage last-sync label (issue #216)', () => {
    it('measures "last sync" from the UTC instant, not the local-parsed one', async () => {
        listSourcesMock.mockResolvedValue([source({ last_sync_at: '2026-08-22T09:03:00' })])
        render(<ImportSourcesPage />)

        // Five hours before "now" — never "Just now", whatever the browser zone.
        expect(await screen.findByText('5h ago')).toBeInTheDocument()
    })

    it('says "Never synced" when the source has never run', async () => {
        listSourcesMock.mockResolvedValue([source()])
        render(<ImportSourcesPage />)

        expect(await screen.findByText('Never synced')).toBeInTheDocument()
    })
})
