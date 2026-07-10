import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BookCard } from './HomePage'

const { coverSrcMock } = vi.hoisted(() => ({ coverSrcMock: vi.fn() }))

vi.mock('../api', () => ({ coverSrc: coverSrcMock }))

beforeEach(() => {
    coverSrcMock.mockReset()
})

describe('HomePage BookCard', () => {
    it('renders the resolved cover image once coverSrc() resolves', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/a.jpg?token=tok')
        render(<BookCard book={{ title: 'A Book', cover_path: '/api/files/covers/a.jpg' }} onPrimary={() => {}} />)

        const img = await screen.findByAltText('A Book')
        expect(img).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok')
    })

    it('shows a placeholder when there is no cover_path', () => {
        render(<BookCard book={{ title: 'A Book', cover_path: null }} onPrimary={() => {}} />)
        expect(screen.queryByRole('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })
})
