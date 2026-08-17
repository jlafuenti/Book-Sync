import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import UnpairedPage, { BookRow } from './UnpairedPage'

const { coverSrcMock, getUnpairedMediaMock } = vi.hoisted(() => ({
    coverSrcMock: vi.fn(),
    getUnpairedMediaMock: vi.fn(),
}))

vi.mock('../api', () => ({
    coverSrc: coverSrcMock,
    getUnpairedMedia: getUnpairedMediaMock,
    createPair: vi.fn(),
}))

beforeEach(() => {
    coverSrcMock.mockReset()
    getUnpairedMediaMock.mockReset()
})

// Issue #120: the manual-pairing page loads only the unpaired set through the
// bounded `getUnpairedMedia()` helper (tab=unpaired on /library/items) — it no
// longer downloads every ebook, audiobook and pair to set-diff them itself.
describe('UnpairedPage data source', () => {
    it('lists the unpaired ebooks and audiobooks from getUnpairedMedia()', async () => {
        getUnpairedMediaMock.mockResolvedValue({
            ebooks: [{ id: 1, title: 'Lonely Ebook', author: 'A', filename: 'e.epub' }],
            audiobooks: [{ id: 2, title: 'Lonely Audio', author: 'B', filename: 'a.m4b' }],
        })
        render(<UnpairedPage />)

        expect(await screen.findByText('Lonely Ebook')).toBeInTheDocument()
        expect(screen.getByText('Lonely Audio')).toBeInTheDocument()
        expect(getUnpairedMediaMock).toHaveBeenCalledTimes(1)
    })
})

describe('UnpairedPage BookRow', () => {
    it('renders the resolved cover image when the item has a cover_path', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/a.jpg?token=tok')
        render(<BookRow item={{ title: 'A Book', cover_path: '/api/files/covers/a.jpg', format: 'epub' }} onSelect={() => {}} />)

        const img = await screen.findByAltText('')
        expect(img).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok')
    })

    it('shows a placeholder when there is no cover_path', () => {
        render(<BookRow item={{ title: 'A Book', cover_path: null, format: 'epub' }} onSelect={() => {}} />)
        expect(screen.queryByRole('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })
})
