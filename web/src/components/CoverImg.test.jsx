import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import CoverImg from './CoverImg'

const { coverSrcMock } = vi.hoisted(() => ({ coverSrcMock: vi.fn() }))

vi.mock('../api', () => ({ coverSrc: coverSrcMock }))

beforeEach(() => {
    coverSrcMock.mockReset()
})

describe('CoverImg', () => {
    it('resolves the async coverSrc() and renders it as the img src', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/book.jpg?token=abc')

        render(<CoverImg path="/api/files/covers/book.jpg" alt="cover" />)

        const img = await screen.findByAltText('cover')
        await waitFor(() => expect(img).toHaveAttribute('src', '/api/files/covers/book.jpg?token=abc'))
        expect(coverSrcMock).toHaveBeenCalledWith('/api/files/covers/book.jpg')
    })

    it('passes through extra img props', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/book.jpg?token=abc')

        render(<CoverImg path="/api/files/covers/book.jpg" alt="cover" className="thumb" loading="lazy" />)

        const img = await screen.findByAltText('cover')
        expect(img).toHaveClass('thumb')
        expect(img).toHaveAttribute('loading', 'lazy')
    })

    it('renders no src for a falsy path without calling coverSrc', () => {
        render(<CoverImg path={null} alt="cover" />)
        const img = screen.getByAltText('cover')
        expect(img).not.toHaveAttribute('src')
        expect(coverSrcMock).not.toHaveBeenCalled()
    })

    // Issue #269: useCoverSrc chained .then() with no .catch(). A token mint
    // that fails — offline, or the server refusing — became one unhandled
    // promise rejection per cover on the page, which is 50 on a library grid.
    it('renders without a src when the token mint fails, and handles the rejection', async () => {
        const onUnhandled = vi.fn()
        process.on('unhandledRejection', onUnhandled)
        try {
            coverSrcMock.mockRejectedValue(new Error('Failed to get media token'))

            render(<CoverImg path="/api/files/covers/book.jpg" alt="cover" />)

            const img = await screen.findByAltText('cover')
            await waitFor(() => expect(coverSrcMock).toHaveBeenCalled())
            // Let any unhandled rejection surface before asserting it did not.
            await new Promise((r) => setTimeout(r, 0))

            expect(img).not.toHaveAttribute('src')
            expect(onUnhandled).not.toHaveBeenCalled()
        } finally {
            process.off('unhandledRejection', onUnhandled)
        }
    })
})
