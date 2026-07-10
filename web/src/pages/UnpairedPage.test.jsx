import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BookRow } from './UnpairedPage'

const { coverSrcMock } = vi.hoisted(() => ({ coverSrcMock: vi.fn() }))

vi.mock('../api', () => ({ coverSrc: coverSrcMock }))

beforeEach(() => {
    coverSrcMock.mockReset()
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
