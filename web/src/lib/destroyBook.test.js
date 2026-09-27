import { describe, it, expect, vi, afterEach } from 'vitest'
import { destroyBook } from './readerRendition'

// Issue #736: epub.js 0.3.93's Stage adds a window "orientationchange"
// listener but its destroy() removes "orientationChange" (wrong case), so every
// destroyed rendition - the reader's, and one per page count - left one behind.

afterEach(() => vi.restoreAllMocks())

function fakeBook() {
    const onOrientation = vi.fn()
    const book = {
        rendition: { manager: { stage: { orientationChangeFunc: onOrientation } } },
        destroy: vi.fn(),
    }
    return { book, onOrientation }
}

describe('destroyBook', () => {
    it('destroys the book and removes the listener epub.js leaves behind', () => {
        const { book, onOrientation } = fakeBook()
        const remove = vi.spyOn(window, 'removeEventListener')

        destroyBook(book)

        expect(book.destroy).toHaveBeenCalledTimes(1)
        expect(remove).toHaveBeenCalledWith('orientationchange', onOrientation, false)
    })

    it('really detaches it: the handler no longer runs on an orientation change', () => {
        const { book, onOrientation } = fakeBook()
        window.addEventListener('orientationchange', onOrientation, false)

        destroyBook(book)
        window.dispatchEvent(new Event('orientationchange'))

        expect(onOrientation).not.toHaveBeenCalled()
    })

    it('copes with a book that has no rendition, and with a destroy that throws', () => {
        expect(() => destroyBook({ destroy: vi.fn() })).not.toThrow()
        expect(() => destroyBook({ destroy: () => { throw new Error('gone') } })).not.toThrow()
        expect(() => destroyBook(null)).not.toThrow()
    })
})
