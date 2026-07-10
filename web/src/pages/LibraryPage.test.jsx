import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BookCard, BookRow } from './LibraryPage'

const { coverSrcMock } = vi.hoisted(() => ({ coverSrcMock: vi.fn() }))

vi.mock('../api', () => ({ coverSrc: coverSrcMock }))

function book(overrides = {}) {
    return {
        id: 1,
        title: 'A Book',
        author: 'An Author',
        mediaType: 'ebook',
        cover_path: '/api/files/covers/a.jpg',
        format: 'epub',
        file_size: 12345,
        uploaded_at: '2026-01-01T00:00:00Z',
        ...overrides,
    }
}

beforeEach(() => {
    coverSrcMock.mockReset()
})

describe('LibraryPage BookCard', () => {
    it('renders the resolved cover image once coverSrc() resolves', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/a.jpg?token=tok')
        render(<BookCard book={book()} onNavigate={() => {}} onSelect={() => {}} onStartSelect={() => {}} onEdit={() => {}} onDelete={() => {}} />)

        const img = await screen.findByAltText('A Book')
        expect(img).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok')
    })

    it('shows a placeholder when there is no cover_path', () => {
        render(<BookCard book={book({ cover_path: null })} onNavigate={() => {}} onSelect={() => {}} onStartSelect={() => {}} onEdit={() => {}} onDelete={() => {}} />)
        expect(screen.queryByRole('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })
})

describe('LibraryPage BookRow', () => {
    it('renders the resolved cover image once coverSrc() resolves', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/a.jpg?token=tok')
        render(<BookRow book={book()} onNavigate={() => {}} onSelect={() => {}} onEdit={() => {}} onDelete={() => {}} />)

        const img = await screen.findByAltText('A Book')
        expect(img).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok')
    })
})
