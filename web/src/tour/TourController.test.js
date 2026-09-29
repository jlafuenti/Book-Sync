import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { TourController } from './TourController'
import { TourAnchorRegistry } from './TourAnchorRegistry'
import { TourAnchors, TourScreens, TourEvents } from './anchors'
import { stepsForRole } from './tourScript'

function rect() {
    return { top: 0, left: 0, right: 10, bottom: 10, width: 10, height: 10, x: 0, y: 0 }
}

function makeApi({ pair = { id: 7, ebook: { id: 99 } }, untouched = true } = {}) {
    return {
        listSyncedPairs: vi.fn().mockResolvedValue(pair ? [pair] : []),
        getPosition: vi.fn().mockResolvedValue(untouched ? null : { epub_chapter: 1 }),
        resetPairProgress: vi.fn().mockResolvedValue(undefined),
    }
}

function makeProgressModeStore(initial = null) {
    let value = initial
    return {
        get: vi.fn(() => value),
        set: vi.fn((v) => { value = v }),
    }
}

async function flush(times = 6) {
    for (let i = 0; i < times; i++) await Promise.resolve()
}

/** Drives the controller's current step forward the way the real UI would. */
function advanceStep(controller) {
    const advance = controller.state.step.advance
    if (advance.kind === 'next' || advance.kind === 'finish') {
        controller.next()
    } else {
        controller.onEvent(advance.event)
    }
}

describe('TourController', () => {
    beforeEach(() => {
        vi.useFakeTimers()
    })

    afterEach(() => {
        vi.useRealTimers()
    })

    it('enters step 0 (preparing) synchronously on start, before the pair resolves', () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        expect(controller.state.status).toBe('preparing')
        expect(controller.state.index).toBe(0)
        expect(controller.state.step.id).toBe('welcome')
        expect(controller.state.total).toBe(stepsForRole('user').length)
    })

    it('resolves to running with the picked pair and willCleanUp once the pair position is known', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: { id: 7 }, untouched: true })
        const controller = new TourController({ registry, api })
        controller.start('user')
        await flush()
        expect(controller.state.status).toBe('running')
        expect(controller.state.pairId).toBe(7)
        expect(controller.state.willCleanUp).toBe(true)
        expect(api.getPosition).toHaveBeenCalledWith('pair', 7)
    })

    it('willCleanUp is false when the pair already has a position (touched)', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: { id: 7 }, untouched: false })
        const controller = new TourController({ registry, api })
        controller.start('user')
        await flush()
        expect(controller.state.willCleanUp).toBe(false)
    })

    it('handles no synced pair at all: pairId stays null, no crash', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: null })
        const controller = new TourController({ registry, api })
        controller.start('user')
        await flush()
        expect(controller.state.status).toBe('running')
        expect(controller.state.pairId).toBeNull()
        expect(controller.state.willCleanUp).toBe(false)
        expect(api.getPosition).not.toHaveBeenCalled()
    })

    it('resolution is Found immediately when the anchor is already registered at entry', async () => {
        const registry = new TourAnchorRegistry()
        registry.set(TourAnchors.HomeContinueReading, rect())
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // welcome -> home_continue_reading
        expect(controller.state.step.id).toBe('home_continue_reading')
        expect(controller.state.resolution).toBe('found')
    })

    it('resolution goes Pending -> Missing 600ms after the screen settles with the anchor absent', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // -> home_continue_reading (anchor absent)
        expect(controller.state.resolution).toBe('pending')

        registry.setSettled(TourScreens.Home, true)
        vi.advanceTimersByTime(599)
        expect(controller.state.resolution).toBe('pending')
        vi.advanceTimersByTime(1)
        expect(controller.state.resolution).toBe('missing')
    })

    it('registry chatter while pending does not postpone Missing (the timer is armed once)', async () => {
        // Seen live on 2026-09-28 at 375px: two same-named nav anchors (the
        // CSS-hidden sidebar and the bottom bar) re-measured every 250 ms and
        // each notification restarted the 600 ms timer, so the step showed
        // "One moment…" forever instead of its emptyBody card.
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // -> home_continue_reading (anchor absent)
        registry.setSettled(TourScreens.Home, true)
        for (let i = 0; i < 4; i++) {
            vi.advanceTimersByTime(200)
            registry.set('SomethingElse', { top: 0, left: i, right: 10 + i, bottom: 10, width: 10, height: 10 })
        }
        expect(controller.state.resolution).toBe('missing')
    })

    it('resolution stays Pending indefinitely while the screen reports loading', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // -> home_continue_reading
        registry.setSettled(TourScreens.Home, false) // loading, not settled
        vi.advanceTimersByTime(60000)
        expect(controller.state.resolution).toBe('pending')
    })

    it('applies a 30s hard cap for a screen that never reports settled at all', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // -> home_continue_reading, screen never settled/loading
        vi.advanceTimersByTime(29999)
        expect(controller.state.resolution).toBe('pending')
        vi.advanceTimersByTime(1)
        expect(controller.state.resolution).toBe('missing')
    })

    it('flips a Missing resolution back to Found once the anchor registers later', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // -> home_continue_reading
        vi.advanceTimersByTime(30000)
        expect(controller.state.resolution).toBe('missing')

        registry.set(TourAnchors.HomeContinueReading, rect())
        expect(controller.state.resolution).toBe('found')
    })

    it('a tapAnchor step ignores next() and only advances on its expected event', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        for (let i = 0; i < 4; i++) controller.next() // welcome..home_recently_added -> home_click_library
        expect(controller.state.step.id).toBe('home_click_library')

        controller.next() // ignored: this step is tapAnchor
        expect(controller.state.step.id).toBe('home_click_library')

        controller.onEvent(TourEvents.routeShown('/series')) // wrong route
        expect(controller.state.step.id).toBe('home_click_library')

        controller.onEvent(TourEvents.routeShown('/library')) // correct
        expect(controller.state.step.id).toBe('library_filters')
    })

    it('a waitFor step advances only on its expected event', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        // The shipped script has no waitFor step any more (reader_toolbar and
        // player_paused became plain next steps so their copy can be read),
        // so give home_next_up a synthetic one before entering it.
        const idx = controller.steps.findIndex((s) => s.id === 'home_next_up')
        controller.steps[idx] = { ...controller.steps[idx], advance: { kind: 'waitFor', event: TourEvents.readerReady(), skippable: false } }
        while (controller.state.step.id !== 'home_next_up') advanceStep(controller)

        controller.onEvent(TourEvents.playerReady()) // unrelated event
        expect(controller.state.step.id).toBe('home_next_up')

        controller.onEvent(TourEvents.readerReady())
        expect(controller.state.step.id).toBe('home_recently_added')
    })

    it('back() moves to the previous step only within the same screen', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        controller.start('user')
        await flush()
        controller.next() // welcome(Home) -> home_continue_reading(Home): same screen
        expect(controller.state.index).toBe(1)
        controller.back()
        expect(controller.state.index).toBe(0)
        expect(controller.state.step.id).toBe('welcome')

        // Cross a screen boundary: details_click_read (Details) -> reader_toolbar (Reader).
        while (controller.state.step.id !== 'reader_toolbar') advanceStep(controller)
        controller.back()
        expect(controller.state.step.id).toBe('reader_toolbar') // unchanged: previous step was a different screen
    })

    it('leaving the reader/player block emits closeOverlays then popToMain', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        const navEvents = []
        controller.subscribeNav((req) => navEvents.push(req))
        controller.start('user')
        await flush()
        while (controller.state.step.id !== 'reader_trick') advanceStep(controller)

        navEvents.length = 0
        advanceStep(controller) // reader_trick (Reader) -> click_series (Details): leaves the block
        expect(controller.state.step.id).toBe('click_series')
        expect(navEvents).toEqual([{ type: 'closeOverlays' }, { type: 'popToMain' }])
    })

    it('does not emit popToMain moving within the reader/player block', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        const navEvents = []
        controller.subscribeNav((req) => navEvents.push(req))
        controller.start('user')
        await flush()
        while (controller.state.step.id !== 'reader_switch_to_audio') advanceStep(controller)
        navEvents.length = 0
        advanceStep(controller) // reader_switch_to_audio (Reader) -> player_paused (Player): still the block
        expect(controller.state.step.id).toBe('player_paused')
        expect(navEvents).toEqual([])
    })

    it('emits a goTo nav request for Home on start, so a replay from Account lands where step 2 lives', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        const navEvents = []
        controller.subscribeNav((req) => navEvents.push(req))
        controller.start('user')
        await flush()
        expect(controller.state.step.id).toBe('welcome')
        expect(navEvents).toContainEqual({ type: 'goTo', route: '/continue' })
    })

    it('emits a goTo nav request for troubleshoot_page (editor role)', async () => {
        const registry = new TourAnchorRegistry()
        const controller = new TourController({ registry, api: makeApi() })
        const navEvents = []
        controller.subscribeNav((req) => navEvents.push(req))
        controller.start('editor')
        await flush()
        while (controller.state.step.id !== 'troubleshoot_page') advanceStep(controller)
        expect(navEvents).toContainEqual({ type: 'goTo', route: '/system/troubleshoot' })
    })

    it('role filtering changes the total step count (user < editor < admin)', () => {
        const userController = new TourController({ registry: new TourAnchorRegistry(), api: makeApi() })
        const editorController = new TourController({ registry: new TourAnchorRegistry(), api: makeApi() })
        const adminController = new TourController({ registry: new TourAnchorRegistry(), api: makeApi() })
        userController.start('user')
        editorController.start('editor')
        adminController.start('admin')
        expect(userController.state.total).toBe(stepsForRole('user').length)
        expect(editorController.state.total).toBe(stepsForRole('editor').length)
        expect(adminController.state.total).toBe(stepsForRole('admin').length)
        expect(userController.state.total).toBeLessThan(editorController.state.total)
        expect(editorController.state.total).toBeLessThan(adminController.state.total)
    })

    it('finishing emits cleanUp and resets progress only when the pair was untouched', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: { id: 7 }, untouched: true })
        const controller = new TourController({ registry, api })
        const navEvents = []
        controller.subscribeNav((req) => navEvents.push(req))
        controller.start('user')
        await flush()
        while (controller.state.status !== 'finished') advanceStep(controller)

        expect(navEvents).toContainEqual({ type: 'cleanUp', pairId: 7 })
        expect(api.resetPairProgress).toHaveBeenCalledWith(7)
    })

    it('does not clean up a pair that already had a position', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: { id: 7 }, untouched: false })
        const controller = new TourController({ registry, api })
        const navEvents = []
        controller.subscribeNav((req) => navEvents.push(req))
        controller.start('user')
        await flush()
        while (controller.state.status !== 'finished') advanceStep(controller)

        expect(navEvents.some((e) => e.type === 'cleanUp')).toBe(false)
        expect(api.resetPairProgress).not.toHaveBeenCalled()
    })

    it('quit() restores the reader progress mode recorded at start', () => {
        const registry = new TourAnchorRegistry()
        const progressModeStore = makeProgressModeStore('percent')
        const controller = new TourController({ registry, api: makeApi(), progressModeStore })
        controller.start('user')
        // Simulate the user changing it mid-tour (e.g. via reader_progress).
        progressModeStore.set('page_in_book')
        expect(progressModeStore.get()).toBe('page_in_book')

        controller.quit()
        expect(progressModeStore.set).toHaveBeenLastCalledWith('percent')
        expect(progressModeStore.get()).toBe('percent')
    })

    it('does not touch the progress mode if it was never changed', () => {
        const registry = new TourAnchorRegistry()
        const progressModeStore = makeProgressModeStore('percent')
        const controller = new TourController({ registry, api: makeApi(), progressModeStore })
        controller.start('user')
        progressModeStore.set.mockClear()
        controller.quit()
        expect(progressModeStore.set).not.toHaveBeenCalled()
    })

    it('uses the injected setTimeout/clearTimeout seam rather than the global timers', async () => {
        const registry = new TourAnchorRegistry()
        const scheduled = []
        const setTimeoutFn = vi.fn((cb, ms) => { const id = { cb, ms }; scheduled.push(id); return id })
        const clearTimeoutFn = vi.fn()
        const controller = new TourController({ registry, api: makeApi(), setTimeoutFn, clearTimeoutFn })
        controller.start('user')
        await flush()
        controller.next() // -> home_continue_reading, anchor absent, screen unreported
        expect(setTimeoutFn).toHaveBeenCalledWith(expect.any(Function), 30000)
    })

    it('adoptPair drops willCleanUp when the new pair’s position lookup fails (never delete on uncertainty)', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: { id: 7 }, untouched: true })
        api.getPosition = vi.fn()
            .mockResolvedValueOnce(null) // start()'s own pick: untouched
            .mockRejectedValueOnce(new Error('503')) // adoptPair's re-check fails
        const controller = new TourController({ registry, api })
        controller.start('user')
        await flush()
        expect(controller.state.willCleanUp).toBe(true)

        controller.adoptPair({ id: 42 })
        await flush()
        expect(controller.state.willCleanUp).toBe(false)
        controller.quit()
        expect(api.resetPairProgress).not.toHaveBeenCalled()
    })

    it('adoptPair re-evaluates willCleanUp for the newly adopted pair', async () => {
        const registry = new TourAnchorRegistry()
        const api = makeApi({ pair: { id: 7 }, untouched: true })
        api.getPosition = vi.fn()
            .mockResolvedValueOnce(null) // start()'s own pick
            .mockResolvedValueOnce({ epub_chapter: 2 }) // adoptPair's re-check
        const controller = new TourController({ registry, api })
        controller.start('user')
        await flush()
        expect(controller.state.willCleanUp).toBe(true)

        controller.adoptPair({ id: 42 })
        expect(controller.state.pairId).toBe(42)
        await flush()
        expect(controller.state.willCleanUp).toBe(false)
    })
})
