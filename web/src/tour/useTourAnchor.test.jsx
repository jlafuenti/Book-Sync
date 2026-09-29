import { describe, it, expect, vi, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { TourAnchorRegistry } from './TourAnchorRegistry'
import { TourRegistryContext, useTourAnchor } from './anchors'

// A control can move without resizing, scrolling or a window resize: the
// sidebar avatar shifts right when the sidebar expands on hover (seen live
// on 2026-09-28, where the hole stayed on the old spot and the click hit the
// blocker). The hook re-measures on a timer, and the registry only notifies
// when the rect actually changed.

function Probe() {
    const ref = useTourAnchor('Foo')
    return <div ref={ref}>foo</div>
}

afterEach(() => vi.useRealTimers())

function Gated({ enabled }) {
    const ref = useTourAnchor('Foo', { enabled })
    return <div ref={ref}>gated</div>
}

describe('useTourAnchor with a disabled twin', () => {
    it('a disabled hook never clears a rect another element registered under the same name', () => {
        // The CSS-hidden sidebar and the bottom bar share anchor names; the
        // disabled side's effect used to clear the name, wiping the other's.
        const registry = new TourAnchorRegistry()
        const original = Element.prototype.getBoundingClientRect
        Element.prototype.getBoundingClientRect = () => ({ top: 0, left: 5, right: 25, bottom: 20, width: 20, height: 20, x: 5, y: 0 })
        try {
            render(
                <TourRegistryContext.Provider value={registry}>
                    <Probe />
                    <Gated enabled={false} />
                </TourRegistryContext.Provider>,
            )
            expect(registry.get('Foo')).not.toBeNull()
        } finally {
            Element.prototype.getBoundingClientRect = original
        }
    })
})

describe('useTourAnchor with a hidden twin', () => {
    it('a zero-size element never clears a rect a visible element registered under the same name', () => {
        const registry = new TourAnchorRegistry()
        const original = Element.prototype.getBoundingClientRect
        Element.prototype.getBoundingClientRect = function () {
            const hidden = this.textContent === 'gated'
            return hidden
                ? { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }
                : { top: 0, left: 5, right: 25, bottom: 20, width: 20, height: 20, x: 5, y: 0 }
        }
        try {
            render(
                <TourRegistryContext.Provider value={registry}>
                    <Probe />
                    <Gated enabled />
                </TourRegistryContext.Provider>,
            )
            expect(registry.get('Foo')?.left).toBe(5)
        } finally {
            Element.prototype.getBoundingClientRect = original
        }
    })
})

describe('useTourAnchor scrolls the wanted control into view', () => {
    it('calls scrollIntoView once when the step wants this anchor, not on later notifications', () => {
        const registry = new TourAnchorRegistry()
        const original = Element.prototype.getBoundingClientRect
        const scroll = vi.fn()
        Element.prototype.getBoundingClientRect = () => ({ top: 0, left: 5, right: 25, bottom: 20, width: 20, height: 20, x: 5, y: 0 })
        Element.prototype.scrollIntoView = scroll
        try {
            render(
                <TourRegistryContext.Provider value={registry}>
                    <Probe />
                </TourRegistryContext.Provider>,
            )
            expect(scroll).not.toHaveBeenCalled()
            registry.setWanted('Foo')
            expect(scroll).toHaveBeenCalledTimes(1)
            registry.setSettled('Home', true) // unrelated chatter
            expect(scroll).toHaveBeenCalledTimes(1)
            registry.setWanted('Bar')
            registry.setWanted('Foo')
            expect(scroll).toHaveBeenCalledTimes(2)
        } finally {
            Element.prototype.getBoundingClientRect = original
            delete Element.prototype.scrollIntoView
        }
    })
})

describe('useTourAnchor re-measure', () => {
    it('picks up a moved element within 300 ms with no resize or scroll event', () => {
        vi.useFakeTimers()
        const registry = new TourAnchorRegistry()
        let left = 10
        const rect = () => ({ top: 0, left, right: left + 20, bottom: 20, width: 20, height: 20, x: left, y: 0 })
        const original = Element.prototype.getBoundingClientRect
        Element.prototype.getBoundingClientRect = function () { return rect() }
        try {
            render(
                <TourRegistryContext.Provider value={registry}>
                    <Probe />
                </TourRegistryContext.Provider>,
            )
            expect(registry.get('Foo').left).toBe(10)
            left = 45
            vi.advanceTimersByTime(300)
            expect(registry.get('Foo').left).toBe(45)
        } finally {
            Element.prototype.getBoundingClientRect = original
        }
    })
})
