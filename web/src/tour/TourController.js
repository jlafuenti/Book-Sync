/**
 * The walkthrough's state machine (issue #598, Track A). Plain JS, no React —
 * mirrors `TourController.kt` on Android: same resolution rules (Pending /
 * Found / Missing), same settle debounce and hard cap, same "back only within
 * a screen" and "leaving the reader/player block pops out" behaviour. Tests
 * drive it directly with fake timers and fake `api`/`registry` doubles.
 */
import { TourEvents, TourScreens } from './anchors'
import { stepsForRole } from './tourScript'

const PROGRESS_MODE_KEY = 'tandem_reader_progress_mode'

function defaultProgressModeStore() {
    return {
        get() {
            try {
                return window.localStorage.getItem(PROGRESS_MODE_KEY)
            } catch {
                return null
            }
        },
        set(value) {
            try {
                if (value === null || value === undefined) window.localStorage.removeItem(PROGRESS_MODE_KEY)
                else window.localStorage.setItem(PROGRESS_MODE_KEY, value)
            } catch {
                // ignore — best-effort only
            }
        },
    }
}

function isReaderOrPlayer(screen) {
    return screen === TourScreens.Reader || screen === TourScreens.Player
}

const INITIAL_STATE = Object.freeze({
    status: 'idle',
    step: null,
    index: -1,
    total: 0,
    resolution: 'pending',
    willCleanUp: false,
    pairId: null,
    canGoBack: false,
})

export class TourController {
    constructor({
        registry,
        api,
        log = () => {},
        progressModeStore,
        setTimeoutFn = (...args) => setTimeout(...args),
        clearTimeoutFn = (...args) => clearTimeout(...args),
        now = () => Date.now(),
        settleMs = 600,
        hardCapMs = 30000,
    }) {
        this.registry = registry
        this.api = api
        this.log = log
        this.progressModeStore = progressModeStore || defaultProgressModeStore()
        this._setTimeout = setTimeoutFn
        this._clearTimeout = clearTimeoutFn
        this._now = now
        this.settleMs = settleMs
        this.hardCapMs = hardCapMs

        this.steps = []
        this.state = INITIAL_STATE
        this._listeners = new Set()
        this._navListeners = new Set()
        this._registryUnsub = null
        this._pendingTimer = null
        this._everFoundForStep = false
        this._savedProgressMode = null
        this._startGeneration = 0
        this._pairId = null
        this._willCleanUp = false
    }

    subscribe(listener) {
        this._listeners.add(listener)
        return () => this._listeners.delete(listener)
    }

    subscribeNav(listener) {
        this._navListeners.add(listener)
        return () => this._navListeners.delete(listener)
    }

    _setState(patch) {
        this.state = { ...this.state, ...patch }
        this._listeners.forEach((listener) => listener(this.state))
    }

    _emitNav(request) {
        this._navListeners.forEach((listener) => listener(request))
    }

    /** Picks a pair (degrading gracefully to no pair) and enters step 0. */
    start(role) {
        this._stopWatching()
        const generation = ++this._startGeneration
        this._role = role
        this.steps = stepsForRole(role)
        this._pairId = null
        this._willCleanUp = false
        this._savedProgressMode = this.progressModeStore.get()
        this.registry.setWantedPairId(null)

        // The welcome card renders at once (issue #642 parity) rather than
        // waiting on the pairs fetch — nothing about step 0 depends on it.
        this._enter(0, true)

        Promise.resolve(this.api.listSyncedPairs())
            .then(async (pairs) => {
                if (generation !== this._startGeneration) return null
                const picked = Array.isArray(pairs) && pairs.length > 0 ? pairs[0] : null
                const pairId = picked ? picked.id : null
                let untouched = false
                if (pairId != null) {
                    const position = await this.api.getPosition('pair', pairId)
                    untouched = position == null
                }
                return { pairId, untouched, generation }
            })
            .then((result) => {
                if (!result || result.generation !== this._startGeneration) return
                this._pairId = result.pairId
                this._willCleanUp = result.untouched
                this.registry.setWantedPairId(result.pairId)
                if (this.state.index === 0) {
                    this._setState({
                        status: 'running',
                        pairId: result.pairId,
                        willCleanUp: result.untouched,
                    })
                }
            })
            .catch(() => {
                if (generation !== this._startGeneration) return
                if (this.state.index === 0) this._setState({ status: 'running' })
            })
    }

    /** Ignored while preparing, and on a tapAnchor step (the card says to tap the anchor instead). */
    next() {
        if (this.state.status !== 'running') return
        if (this.state.step?.advance.kind === 'tapAnchor') return
        this._advanceFrom(this.state.index)
    }

    /** Steps back within the current screen only — see class doc. */
    back() {
        if (this.state.status !== 'running') return
        if (!this._canGoBack(this.state.index)) return
        this._enter(this.state.index - 1)
    }

    _canGoBack(index) {
        return index > 0 && this.steps[index - 1].screen === this.steps[index].screen
    }

    quit() {
        this._finish()
    }

    /** A no-op unless the current step is a skippable waitFor. */
    skip() {
        if (this.state.status !== 'running') return
        const advance = this.state.step?.advance
        if (!advance || advance.kind !== 'waitFor' || !advance.skippable) return
        this._advanceFrom(this.state.index)
    }

    /**
     * A screen found a better pair than the one the walkthrough started with
     * (e.g. the Library page adopts the first synced pair in its own
     * ordering). Re-evaluates willCleanUp for the newly adopted pair.
     */
    adoptPair(pair) {
        const pairId = pair ? pair.id : null
        this._pairId = pairId
        this.registry.setWantedPairId(pairId)
        this._setState({ pairId })
        if (pairId == null) {
            this._willCleanUp = false
            this._setState({ willCleanUp: false })
            return
        }
        // Until the new pair's position is known, assume it has one: a
        // cleanup that runs on a guess deletes real reading progress.
        this._willCleanUp = false
        this._setState({ willCleanUp: false })
        Promise.resolve(this.api.getPosition('pair', pairId))
            .then((position) => {
                if (this._pairId !== pairId) return
                this._willCleanUp = position == null
                this._setState({ willCleanUp: this._willCleanUp })
            })
            .catch(() => {})
    }

    /** Advances a tapAnchor/waitFor step whose expected event just fired. */
    onEvent(event) {
        if (this.state.status !== 'running') return
        const advance = this.state.step?.advance
        if (!advance) return
        if (advance.kind !== 'tapAnchor' && advance.kind !== 'waitFor') return
        if (TourEvents.matchesKind(advance.event, event)) {
            this._advanceFrom(this.state.index)
        }
    }

    _advanceFrom(index) {
        const leaving = this.steps[index]
        if (index >= this.steps.length - 1) {
            this._finish()
            return
        }
        const nextIndex = index + 1
        const nextStep = this.steps[nextIndex]
        if (isReaderOrPlayer(leaving.screen) && !isReaderOrPlayer(nextStep.screen)) {
            this._emitNav({ type: 'closeOverlays' })
            this._emitNav({ type: 'popToMain' })
        }
        this._enter(nextIndex)
    }

    _enter(index, preparing = false) {
        this._stopWatching()
        const step = this.steps[index]
        this.registry.setWanted(step.anchor)
        this._stepEnteredAt = this._now()

        const immediateRect = step.anchor ? this.registry.get(step.anchor) : null
        this._everFoundForStep = !!immediateRect
        const resolution = !step.anchor || immediateRect ? 'found' : 'pending'

        this._setState({
            status: preparing ? 'preparing' : 'running',
            index,
            step,
            total: this.steps.length,
            resolution,
            willCleanUp: this._willCleanUp,
            pairId: this._pairId,
            canGoBack: this._canGoBack(index),
        })

        if (step.anchor) this._watchAnchor(index, step)
        if (step.goTo) this._emitNav({ type: 'goTo', route: step.goTo })
    }

    _watchAnchor(index, step) {
        const check = () => {
            if (this.state.index !== index) {
                this._stopWatching()
                return
            }
            const rect = this.registry.get(step.anchor)
            if (rect) {
                this._clearPendingTimer()
                this._everFoundForStep = true
                if (this.state.resolution !== 'found') {
                    this._logTransition(step, this.state.resolution, 'found')
                    this._setState({ resolution: 'found' })
                }
                return
            }
            if (this._everFoundForStep) {
                // Past the first Found, a vanished anchor almost always means
                // the tap landed and the next screen is on its way in — wait
                // indefinitely rather than reapplying the hard cap.
                this._clearPendingTimer()
                if (this.state.resolution !== 'pending') {
                    this._logTransition(step, this.state.resolution, 'pending')
                    this._setState({ resolution: 'pending' })
                }
                return
            }
            // A Missing verdict stands until the anchor actually appears (the
            // `rect` branch above flips it back to Found); registry chatter
            // must not bounce it through Pending and re-arm the timer.
            if (this.state.resolution === 'missing') return
            if (this.state.resolution !== 'pending') {
                this._logTransition(step, this.state.resolution, 'pending')
                this._setState({ resolution: 'pending' })
            }
            const screenState = this.registry.screenState(step.screen)
            if (screenState === 'loading') {
                this._clearPendingTimer()
                return // waited for however long that takes
            }
            const delay = screenState === 'settled' ? this.settleMs : this.hardCapMs
            // Registry notifications arrive constantly (every anchor
            // re-measure), and each one runs this check: the timer is armed
            // once and only re-armed when the delay it needs has changed,
            // or a Missing verdict would be postponed indefinitely.
            if (this._pendingTimer && this._pendingTimerDelay === delay) return
            this._clearPendingTimer()
            this._pendingTimerDelay = delay
            this._pendingTimer = this._setTimeout(() => {
                this._pendingTimer = null
                if (this.state.index !== index || this._everFoundForStep) return
                this._logTransition(step, this.state.resolution, 'missing')
                this._setState({ resolution: 'missing' })
            }, delay)
        }
        this._registryUnsub = this.registry.subscribe(check)
        check()
    }

    _logTransition(step, from, to) {
        if (from === to) return
        const elapsedMs = this._now() - this._stepEnteredAt
        this.log(`${step.id}: ${from}→${to} after ${elapsedMs}ms`)
    }

    _clearPendingTimer() {
        if (this._pendingTimer) {
            this._clearTimeout(this._pendingTimer)
            this._pendingTimer = null
        }
        this._pendingTimerDelay = null
    }

    _stopWatching() {
        this._clearPendingTimer()
        if (this._registryUnsub) {
            this._registryUnsub()
            this._registryUnsub = null
        }
    }

    _finish() {
        const running = this.state.status === 'running' || this.state.status === 'preparing'
        if (running && this.state.step && isReaderOrPlayer(this.state.step.screen)) {
            this._emitNav({ type: 'closeOverlays' })
            this._emitNav({ type: 'popToMain' })
        }

        const current = this.progressModeStore.get()
        if (current !== this._savedProgressMode) {
            this.progressModeStore.set(this._savedProgressMode)
        }

        if (this._willCleanUp && this._pairId != null) {
            Promise.resolve(this.api.resetPairProgress(this._pairId)).catch(() => {})
            this._emitNav({ type: 'cleanUp', pairId: this._pairId })
        }

        this._stopWatching()
        this.registry.setWanted(null)
        this.registry.setWantedPairId(null)
        this._setState({ status: 'finished' })
    }
}
