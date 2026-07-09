import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { SeriesCard, SeriesListRow, SeriesBookRow } from './SeriesPage'

const { coverSrcMock } = vi.hoisted(() => ({ coverSrcMock: vi.fn() }))

vi.mock('../api', () => ({ coverSrc: coverSrcMock }))

function group(overrides = {}) {
    return {
        name: 'A Series',
        author: 'An Author',
        items: [{ key: '1', type: 'ebook', hasEbook: true, hasAudiobook: false, title: 'Book 1', seriesIndex: 1 }],
        covers: ['/api/files/covers/a.jpg'],
        ...overrides,
    }
}

beforeEach(() => {
    coverSrcMock.mockReset().mockResolvedValue('/api/files/covers/a.jpg?token=tok')
})

describe('SeriesPage SeriesCard', () => {
    it('renders the resolved cover image for the series stack', async () => {
        const { container } = render(
            <SeriesCard
                group={group()}
                selectMode={false}
                selectedKeys={new Set()}
                onToggleSelect={() => {}}
                onStartSelect={() => {}}
                onClick={() => {}}
                onAuthorClick={() => {}}
            />
        )
        await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok'))
    })
})

describe('SeriesPage SeriesListRow', () => {
    it('renders the resolved cover image for the row thumbnail', async () => {
        const { container } = render(
            <SeriesListRow
                group={group()}
                selectMode={false}
                selectedKeys={new Set()}
                onToggleSelect={() => {}}
                isExpanded={false}
                onToggleExpand={() => {}}
                onSeriesClick={() => {}}
                onAuthorClick={() => {}}
            />
        )
        await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok'))
    })
})

describe('SeriesPage SeriesBookRow', () => {
    it('renders the resolved cover image when the item has a cover_path', async () => {
        const { container } = render(<SeriesBookRow item={{ title: 'Book 1', type: 'ebook', cover_path: '/api/files/covers/a.jpg' }} />)
        await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok'))
    })

    it('shows a placeholder when there is no cover_path', () => {
        const { container } = render(<SeriesBookRow item={{ title: 'Book 1', type: 'ebook', cover_path: null }} />)
        expect(container.querySelector('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })
})
