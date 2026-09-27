import { describe, it, expect, vi } from 'vitest'
import { TourAnchorRegistry } from './TourAnchorRegistry'

function rect(w = 10, h = 10) {
    return { top: 0, left: 0, right: w, bottom: h, width: w, height: h, x: 0, y: 0 }
}

describe('TourAnchorRegistry', () => {
    it('stores and retrieves a rect by name', () => {
        const registry = new TourAnchorRegistry()
        registry.set('Foo', rect())
        expect(registry.get('Foo')).toEqual(rect())
    })

    it('returns null for an anchor that was never set', () => {
        const registry = new TourAnchorRegistry()
        expect(registry.get('Nope')).toBeNull()
    })

    it('drops a zero-sized rect instead of storing it', () => {
        const registry = new TourAnchorRegistry()
        registry.set('Foo', rect(0, 0))
        expect(registry.get('Foo')).toBeNull()
        registry.set('Bar', rect(10, 0))
        expect(registry.get('Bar')).toBeNull()
        registry.set('Baz', rect(0, 10))
        expect(registry.get('Baz')).toBeNull()
    })

    it('clears an anchor', () => {
        const registry = new TourAnchorRegistry()
        registry.set('Foo', rect())
        registry.clear('Foo')
        expect(registry.get('Foo')).toBeNull()
    })

    it('notifies subscribers when a rect is set or cleared', () => {
        const registry = new TourAnchorRegistry()
        const listener = vi.fn()
        const unsubscribe = registry.subscribe(listener)

        registry.set('Foo', rect())
        expect(listener).toHaveBeenCalledTimes(1)

        registry.clear('Foo')
        expect(listener).toHaveBeenCalledTimes(2)

        unsubscribe()
        registry.set('Foo', rect())
        expect(listener).toHaveBeenCalledTimes(2)
    })

    it('does not notify when setting a zero-sized rect (nothing changed)', () => {
        const registry = new TourAnchorRegistry()
        const listener = vi.fn()
        registry.subscribe(listener)
        registry.set('Foo', rect(0, 0))
        expect(listener).not.toHaveBeenCalled()
    })

    it('tracks screen settle state: unreported, loading, settled', () => {
        const registry = new TourAnchorRegistry()
        expect(registry.screenState('Home')).toBe('unreported')

        registry.setSettled('Home', false)
        expect(registry.screenState('Home')).toBe('loading')

        registry.setSettled('Home', true)
        expect(registry.screenState('Home')).toBe('settled')
    })

    it('clearScreen resets a screen back to unreported', () => {
        const registry = new TourAnchorRegistry()
        registry.setSettled('Home', true)
        registry.clearScreen('Home')
        expect(registry.screenState('Home')).toBe('unreported')
    })

    it('setWanted records and exposes the wanted anchor name', () => {
        const registry = new TourAnchorRegistry()
        registry.setWanted('Foo')
        expect(registry.wanted).toBe('Foo')
    })

    it('setWantedPairId records and exposes the wanted pair id', () => {
        const registry = new TourAnchorRegistry()
        expect(registry.wantedPairId).toBeNull()
        registry.setWantedPairId(42)
        expect(registry.wantedPairId).toBe(42)
    })

    it('notifies subscribers on setSettled/clearScreen/setWanted/setWantedPairId', () => {
        const registry = new TourAnchorRegistry()
        const listener = vi.fn()
        registry.subscribe(listener)

        registry.setSettled('Home', true)
        registry.clearScreen('Home')
        registry.setWanted('Foo')
        registry.setWantedPairId(1)

        expect(listener).toHaveBeenCalledTimes(4)
    })
})
