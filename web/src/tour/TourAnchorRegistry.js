/**
 * Where every real, on-screen tour anchor is right now (issue #598, Track A).
 *
 * Plain JS, no React — `useTourAnchor` (anchors.js) writes into it as controls
 * mount/move/unmount, and `TourController` reads it to decide whether a step's
 * spotlighted control exists yet. Mirrors the shape of Android's
 * `TourAnchorRegistry` (rects + settled screens + a "wanted" anchor/pair) so
 * the same mental model applies on both platforms.
 */
export class TourAnchorRegistry {
    constructor() {
        this._rects = new Map()
        this._settled = new Map()
        this._listeners = new Set()
        this._wanted = null
        this._wantedPairId = null
    }

    /** The rect for `name`, or null if it isn't currently on screen. */
    get(name) {
        return this._rects.get(name) ?? null
    }

    /**
     * Records `name`'s bounding rect. A zero-sized rect (display:none, an
     * element mid-unmount, a layout pass that hasn't run yet) is dropped
     * rather than stored — the same rule Android's registry applies — so a
     * step never spotlights an invisible box.
     */
    set(name, rectValue) {
        if (!rectValue || rectValue.width <= 0 || rectValue.height <= 0) {
            this.clear(name)
            return
        }
        this._rects.set(name, rectValue)
        this._notify()
    }

    clear(name) {
        if (this._rects.delete(name)) this._notify()
    }

    /** Screen settle state: 'unreported' | 'loading' | 'settled'. */
    screenState(screen) {
        if (!this._settled.has(screen)) return 'unreported'
        return this._settled.get(screen) ? 'settled' : 'loading'
    }

    setSettled(screen, settled) {
        this._settled.set(screen, !!settled)
        this._notify()
    }

    clearScreen(screen) {
        this._settled.delete(screen)
        this._notify()
    }

    get wanted() {
        return this._wanted
    }

    setWanted(name) {
        this._wanted = name ?? null
        this._notify()
    }

    get wantedPairId() {
        return this._wantedPairId
    }

    setWantedPairId(pairId) {
        this._wantedPairId = pairId ?? null
        this._notify()
    }

    /** `listener()` fires on every change; returns an unsubscribe function. */
    subscribe(listener) {
        this._listeners.add(listener)
        return () => this._listeners.delete(listener)
    }

    _notify() {
        this._listeners.forEach((listener) => listener())
    }
}
