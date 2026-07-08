import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import useIsMobile from './useIsMobile'
import { setViewport } from '../test/setup'

// useIsMobile drives every desktop-vs-mobile rendering difference in the app, so
// pin its breakpoint behavior (max-width: 768px).
describe('useIsMobile', () => {
    it('is true on a narrow (mobile) viewport', () => {
        setViewport(500)
        const { result } = renderHook(() => useIsMobile())
        expect(result.current).toBe(true)
    })

    it('is false on a wide (desktop) viewport', () => {
        setViewport(1200)
        const { result } = renderHook(() => useIsMobile())
        expect(result.current).toBe(false)
    })

    it('treats exactly 768px as mobile (inclusive breakpoint)', () => {
        setViewport(768)
        const { result } = renderHook(() => useIsMobile())
        expect(result.current).toBe(true)
    })
})
