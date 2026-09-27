import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { updateMe } from '../api/auth'
import { getPairs } from '../api/library'
import { getPosition, resetPairProgress } from '../api/sync'
import { TourController } from './TourController'
import { TourAnchorRegistry } from './TourAnchorRegistry'
import { TourEvents, TourRegistryContext, TourControllerContext } from './anchors'
import { TourStateContext } from './TourContext'
import TourOffer from './TourOffer'

export { useTour } from './TourContext'

/** The pairs the walkthrough is allowed to pick from: synced ones only —
 *  an unsynced pair has no sync map, so the reader/player loop the script
 *  walks through (details_click_read..reader_trick) would have nothing to
 *  demonstrate. */
async function listSyncedPairs() {
    const pairs = await getPairs()
    return pairs.filter((pair) => pair.status === 'synced')
}

/**
 * Wires the tour engine into the app (issue #598, Track A): owns the
 * registry/controller pair, executes the controller's navigation requests
 * with the real router, reports every route change as a `routeShown` event,
 * and owns the one-time offer (`user.web_tour_offered_at === null`).
 *
 * `user.web_tour_offered_at` arrives from Track C (server); `undefined`
 * (an older server that doesn't send the field yet) is deliberately treated
 * as "not eligible" rather than "eligible" — the offer must never appear
 * against a server that doesn't understand it — so only a literal `null`
 * shows the offer.
 */
export function TourProvider({ children }) {
    const { user } = useAuth()
    const navigate = useNavigate()
    const location = useLocation()

    const registryRef = useRef(null)
    if (!registryRef.current) registryRef.current = new TourAnchorRegistry()
    const registry = registryRef.current

    // Caches the last listSyncedPairs() result so nav handlers (openReader,
    // openPlayer, popToMain) can resolve a pairId to the ebook id the route
    // needs, without a second round trip.
    const pairsCacheRef = useRef([])

    const controllerRef = useRef(null)
    if (!controllerRef.current) {
        controllerRef.current = new TourController({
            registry,
            api: {
                listSyncedPairs: async () => {
                    const pairs = await listSyncedPairs()
                    pairsCacheRef.current = pairs
                    return pairs
                },
                getPosition,
                resetPairProgress,
            },
        })
    }
    const controller = controllerRef.current

    const [state, setState] = useState(controller.state)
    useEffect(() => controller.subscribe(setState), [controller])

    useEffect(() => {
        const findEbookId = (pairId) => {
            const pair = pairsCacheRef.current.find((p) => p.id === pairId)
            return pair?.ebook?.id ?? null
        }
        return controller.subscribeNav((request) => {
            switch (request.type) {
                case 'goTo':
                    navigate(request.route)
                    break
                case 'openReader': {
                    const ebookId = findEbookId(request.pairId)
                    if (ebookId != null) navigate(`/book/ebook/${ebookId}`, { state: { openReader: true } })
                    break
                }
                case 'openPlayer': {
                    const ebookId = findEbookId(request.pairId)
                    if (ebookId != null) navigate(`/book/ebook/${ebookId}`, { state: { openPlayer: true } })
                    break
                }
                case 'popToMain': {
                    const ebookId = findEbookId(state.pairId)
                    if (ebookId != null) navigate(`/book/ebook/${ebookId}`)
                    break
                }
                case 'closeOverlays':
                    // Nothing to do at this layer — the reader/player surface
                    // itself (Track B) listens for this to tear itself down.
                    break
                case 'cleanUp':
                    // The server-side reset already happened inside the
                    // controller (it calls api.resetPairProgress directly);
                    // this is only a signal for anything else that needs to
                    // know the tour is done with this pair.
                    break
                default:
                    break
            }
            // eslint-disable-next-line react-hooks/exhaustive-deps
        })
    }, [controller, navigate, state.pairId])

    // Every route change is a tour event, whether the tour drove it (a
    // tapAnchor step's own click) or the user navigated some other way.
    useEffect(() => {
        controller.onEvent(TourEvents.routeShown(location.pathname))
    }, [controller, location.pathname])

    const [offerAnswered, setOfferAnswered] = useState(false)
    const showOffer = !offerAnswered && user?.web_tour_offered_at === null

    const answerOffer = () => {
        setOfferAnswered(true)
        updateMe({ web_tour_offered: true }).catch(() => {})
    }

    const tourValue = useMemo(() => ({
        state,
        start: () => controller.start(user?.role),
        next: () => controller.next(),
        back: () => controller.back(),
        quit: () => controller.quit(),
        skip: () => controller.skip(),
        replay: () => controller.start(user?.role),
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [state, controller, user?.role])

    return (
        <TourRegistryContext.Provider value={registry}>
            <TourControllerContext.Provider value={controller}>
                <TourStateContext.Provider value={tourValue}>
                    {children}
                    {showOffer && (
                        <TourOffer
                            onNotNow={answerOffer}
                            onTakeTour={() => {
                                answerOffer()
                                controller.start(user?.role)
                            }}
                        />
                    )}
                </TourStateContext.Provider>
            </TourControllerContext.Provider>
        </TourRegistryContext.Provider>
    )
}
