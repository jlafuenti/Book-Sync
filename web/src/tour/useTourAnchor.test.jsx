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
