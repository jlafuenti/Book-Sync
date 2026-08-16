import { describe, it, expect, vi } from 'vitest'
import { registerServiceWorker } from './registerSw'

// Issue #62: the service worker precaches the app shell so an installed
// Tandem opens instantly (and to the login/home screen even offline). It is
// registered only in production builds — dev and test never see it, so the
// Vite dev server's HMR and vitest's jsdom stay SW-free — and only where the
// browser supports it.
describe('registerServiceWorker', () => {
    it('registers in production when the browser supports service workers', async () => {
        const registerSW = vi.fn()
        const load = vi.fn(async () => ({ registerSW }))
        const nav = { serviceWorker: {} }

        expect(registerServiceWorker({ nav, prod: true, load })).toBe(true)
        await vi.waitFor(() => expect(registerSW).toHaveBeenCalledWith({ immediate: true }))
    })

    it('is a no-op outside production', () => {
        const load = vi.fn()
        expect(registerServiceWorker({ nav: { serviceWorker: {} }, prod: false, load })).toBe(false)
        expect(load).not.toHaveBeenCalled()
    })

    it('is a no-op when the browser has no serviceWorker', () => {
        const load = vi.fn()
        expect(registerServiceWorker({ nav: {}, prod: true, load })).toBe(false)
        expect(registerServiceWorker({ nav: undefined, prod: true, load })).toBe(false)
        expect(load).not.toHaveBeenCalled()
    })

    it('swallows a failed load — a broken SW must never break the app', async () => {
        const load = vi.fn(async () => { throw new Error('boom') })
        expect(registerServiceWorker({ nav: { serviceWorker: {} }, prod: true, load })).toBe(true)
        await vi.waitFor(() => expect(load).toHaveBeenCalled())
    })
})
