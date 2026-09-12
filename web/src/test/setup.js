import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup, configure } from '@testing-library/react'

// Unmount React trees between tests.
afterEach(() => cleanup())

// Issue #471: Testing Library's default is 1000ms per findBy*/waitFor, which is
// ample on a developer machine and not ample on a 2-core CI runner sharing
// itself with a worker per core. The suite had been failing a *different* test
// on each run — SystemPage on one PR, TranscriptionPage.retry twice on main —
// on diffs that could not have caused it.
//
// This weakens no assertion: a passing test still resolves as fast as it ever
// did, and only a failing one waits longer before reporting. The cost is that a
// genuinely broken test takes 5s rather than 1s to say so.
//
// It does NOT cover a synchronous getBy* that runs before the render it needs
// has committed — those never consult this value. Those are fixed per site.
configure({ asyncUtilTimeout: 5000 })

// jsdom has no media engine — HTMLMediaElement methods throw "Not implemented"
// (e.g. AudioPlayerContext calling audio.pause() on unmount). Stub them.
window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve())
window.HTMLMediaElement.prototype.pause = vi.fn()
window.HTMLMediaElement.prototype.load = vi.fn()

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
