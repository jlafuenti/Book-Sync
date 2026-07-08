import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// Unmount React trees between tests.
afterEach(() => cleanup())

/**
 * jsdom has no layout engine, so window.matchMedia is undefined. Install a mock
 * driven by a viewport width, so components using useIsMobile() (which queries
 * `(max-width: 768px)`) can be rendered as desktop or mobile.
 *
 * Usage in a test:  setViewport(500)  // mobile   |  setViewport(1200) // desktop
 */
export function setViewport(width) {
    window.innerWidth = width
    window.matchMedia = vi.fn().mockImplementation((query) => {
        const max = /max-width:\s*(\d+)px/.exec(query)
        const matches = max ? width <= Number(max[1]) : false
        return {
            matches,
            media: query,
            onchange: null,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            addListener: vi.fn(),      // deprecated, some libs still call it
            removeListener: vi.fn(),
            dispatchEvent: vi.fn(),
        }
    })
}

// Default to desktop unless a test overrides it.
setViewport(1200)
