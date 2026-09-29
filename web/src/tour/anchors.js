/**
 * The public interface of the web guided walkthrough (issue #598, Track A).
 *
 * Track B (page anchors) imports `TourAnchors`/`useTourAnchor`/`useTourScreen`
 * from here to tag the real controls the script spotlights; any page can pull
 * in `useTourEmit` to report a tour event (a route change, a details/reader/
 * player screen opening, a progress-mode change) without importing the
 * controller directly. Mirrors `TourScript.kt` / `TourController.kt` on
 * Android — same vocabulary, same event-matching rule — so the copy and the
 * state machine stay in step across platforms.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { TourAnchorRegistry } from './TourAnchorRegistry'

// Every real control the walkthrough can spotlight. Track A owns the nav
// links (App.jsx); every other anchor here is tagged by Track B once its
// page work lands — until then the step that names it simply resolves to
// "Missing" (see TourController), which the overlay explains via emptyBody.
export const TourAnchors = Object.freeze({
    HomeContinueReading: 'HomeContinueReading',
    HomeNextUp: 'HomeNextUp',
    HomeRecentlyAdded: 'HomeRecentlyAdded',
    NavLibrary: 'NavLibrary',
    NavSeries: 'NavSeries',
    NavTranscription: 'NavTranscription',
    NavSystem: 'NavSystem',
    NavAccount: 'NavAccount',
    LibraryFilterPills: 'LibraryFilterPills',
    LibrarySortSearch: 'LibrarySortSearch',
    LibraryMaintenance: 'LibraryMaintenance',
    LibraryPairCard: 'LibraryPairCard',
    DetailsPairedCard: 'DetailsPairedCard',
    DetailsPrimaryAction: 'DetailsPrimaryAction',
    ReaderToolbar: 'ReaderToolbar',
    ReaderProgress: 'ReaderProgress',
    ReaderSwitchToAudio: 'ReaderSwitchToAudio',
    ReaderPage: 'ReaderPage',
    PlayerTransport: 'PlayerTransport',
    PlayerSwitchToReader: 'PlayerSwitchToReader',
    SeriesGrid: 'SeriesGrid',
    TranscriptionTabs: 'TranscriptionTabs',
    TranscriptionQueueAll: 'TranscriptionQueueAll',
    TroubleshootHeader: 'TroubleshootHeader',
    SystemStatus: 'SystemStatus',
    SystemTroubleshootCard: 'SystemTroubleshootCard',
    SystemTranscriptionSettings: 'SystemTranscriptionSettings',
    SystemUserManagement: 'SystemUserManagement',
    SystemBackups: 'SystemBackups',
    AccountReplayTour: 'AccountReplayTour',
})

export const TourScreens = Object.freeze({
    Home: 'Home',
    Library: 'Library',
    Details: 'Details',
    Reader: 'Reader',
    Player: 'Player',
    Series: 'Series',
    Transcription: 'Transcription',
    Troubleshoot: 'Troubleshoot',
    System: 'System',
    Account: 'Account',
})

/**
 * Something the app reports back to the controller as the user (or the app on
 * the user's behalf) does the guided thing a step is waiting for. Every
 * constructor returns a plain `{ kind, ... }` object; `matchesKind` is the one
 * place that decides whether an incoming event satisfies a step's expected
 * one — the same rule as Kotlin's `TourEvent.matchesKind`: pair-carrying kinds
 * match on kind alone (the pair id in a statically-built step is a
 * placeholder), `routeShown` also compares its route.
 */
export const TourEvents = Object.freeze({
    routeShown: (route) => ({ kind: 'routeShown', route }),
    detailsOpened: (pairId = null) => ({ kind: 'detailsOpened', pairId }),
    readerOpened: (pairId = null) => ({ kind: 'readerOpened', pairId }),
    readerReady: () => ({ kind: 'readerReady' }),
    readerProgressModeChanged: () => ({ kind: 'readerProgressModeChanged' }),
    playerOpened: (pairId = null) => ({ kind: 'playerOpened', pairId }),
    playerReady: () => ({ kind: 'playerReady' }),
    matchesKind(expected, actual) {
        if (!expected || !actual) return false
        if (expected.kind !== actual.kind) return false
        if (expected.kind === 'routeShown') return expected.route === actual.route
        return true
    },
})

// A single shared registry/controller pair is the default so that a page
// component rendered outside <TourProvider> (most page unit tests) still
// works: useTourAnchor/useTourEmit become harmless no-ops instead of
// crashing. TourProvider supplies its own instances via these contexts.
const defaultRegistry = new TourAnchorRegistry()

export const TourRegistryContext = createContext(defaultRegistry)
export const TourControllerContext = createContext(null)

/**
 * Puts a ref on an element to register it as a named tour anchor. Re-measures
 * on ResizeObserver, window resize and scroll (capture phase, passive) and on
 * a 250 ms timer for moves none of those report, and unregisters on unmount
 * or whenever `enabled` is false.
 *
 * `pairId`, when given, additionally gates registration on
 * `registry.wantedPairId` — this is how `LibraryPairCard` registers only for
 * the one pair the tour actually picked, out of a whole list of rendered
 * cards that all call this hook.
 *
 * Also stamps a `data-tour="<name>"` attribute on the element, purely for
 * test/debugging visibility (App.test.jsx asserts the nav links carry it).
 */
export function useTourAnchor(name, { enabled = true, pairId } = {}) {
    const registry = useContext(TourRegistryContext)
    const [node, setNode] = useState(null)
    const [, forceTick] = useState(0)

    // Re-evaluate `active` (below) whenever the registry's wanted-pair
    // changes — the registry itself carries no React state, so a plain
    // subscription is what drives the re-render.
    useEffect(() => {
        if (pairId === undefined) return undefined
        return registry.subscribe(() => forceTick((t) => t + 1))
    }, [registry, pairId])

    const active = enabled && (pairId === undefined || pairId === registry.wantedPairId)

    useEffect(() => {
        if (!active || !node) {
            registry.clear(name)
            return undefined
        }
        const measure = () => registry.set(name, node.getBoundingClientRect())
        measure()
        const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
        ro?.observe(node)
        window.addEventListener('resize', measure)
        window.addEventListener('scroll', measure, { capture: true, passive: true })
        // A control can move without any of those firing — the sidebar
        // avatar shifts when the sidebar expands on hover, a card slides as
        // a sibling appears — so re-measure on a timer too. The registry
        // ignores an unchanged rect, so this costs nothing when still.
        const timer = setInterval(measure, 250)
        return () => {
            ro?.disconnect()
            window.removeEventListener('resize', measure)
            window.removeEventListener('scroll', measure, true)
            clearInterval(timer)
            registry.clear(name)
        }
    }, [active, name, node, registry])

    return useCallback((el) => {
        setNode(el)
        if (el) el.setAttribute('data-tour', name)
    }, [name])
}

/**
 * Reports a screen's load state to the registry for as long as the component
 * calling it is mounted: `setSettled(screen, settled)` on every render where
 * `settled` changes, `clearScreen(screen)` on unmount. `settled` is whatever
 * the page considers "done loading" — e.g. its data fetch resolved.
 */
export function useTourScreen(screen, settled) {
    const registry = useContext(TourRegistryContext)
    useEffect(() => {
        registry.setSettled(screen, !!settled)
        return () => registry.clearScreen(screen)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [registry, screen, settled])
}

/** `(event) => controller.onEvent(event)` — a no-op outside <TourProvider>. */
export function useTourEmit() {
    const controller = useContext(TourControllerContext)
    const controllerRef = useRef(controller)
    controllerRef.current = controller
    return useCallback((event) => {
        controllerRef.current?.onEvent(event)
    }, [])
}
