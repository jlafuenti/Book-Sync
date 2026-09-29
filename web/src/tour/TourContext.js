/**
 * The `useTour()` hook's context (issue #598, Track A).
 *
 * Split out of `TourProvider.jsx` so `TourOverlay.jsx`/`TourOffer.jsx` and
 * `TourProvider.jsx` itself can both import it without an import cycle
 * (`TourProvider` renders `TourOverlay`, and `TourOverlay` calls `useTour()`).
 *
 * Outside a `<TourProvider>` (most page unit tests, and `AccountPage.jsx`'s
 * own test file), `useTour()` returns an idle, side-effect-free stand-in
 * rather than throwing — the same "harmless outside the provider" rule
 * `anchors.js` follows for `useTourAnchor`/`useTourEmit`.
 */
import { createContext, useContext } from 'react'

const noop = () => {}

const FALLBACK = Object.freeze({
    state: Object.freeze({
        status: 'idle', step: null, index: -1, total: 0,
        resolution: 'pending', willCleanUp: false, pairId: null, canGoBack: false,
    }),
    start: noop,
    next: noop,
    back: noop,
    quit: noop,
    skip: noop,
    replay: noop,
    adoptPair: noop,
})

export const TourStateContext = createContext(null)

export function useTour() {
    return useContext(TourStateContext) || FALLBACK
}
