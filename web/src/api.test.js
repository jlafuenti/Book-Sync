import { describe, it, expect, vi, beforeEach } from 'vitest'

// api.js reads/writes tokens as module-level state, seeded once from
// localStorage at import time. Reset the module registry per test so each
// test gets a fresh read of localStorage instead of a cached singleton.
beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
    vi.unstubAllGlobals()
})

describe('logout()', () => {
    it('calls the server logout endpoint and clears local tokens on success', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        localStorage.setItem('tandem_refresh', 'refresh-1')

        const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
        vi.stubGlobal('fetch', fetchMock)

        const { logout, isLoggedIn } = await import('./api')
        expect(isLoggedIn()).toBe(true)

        await logout()

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/auth/logout',
            expect.objectContaining({ method: 'POST' }),
        )
        expect(isLoggedIn()).toBe(false)
        expect(localStorage.getItem('tandem_token')).toBeNull()
        expect(localStorage.getItem('tandem_refresh')).toBeNull()
    })

    it('still clears local tokens when the server call fails (e.g. offline)', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        localStorage.setItem('tandem_refresh', 'refresh-1')

        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))

        const { logout, isLoggedIn } = await import('./api')
        await logout()

        expect(isLoggedIn()).toBe(false)
        expect(localStorage.getItem('tandem_token')).toBeNull()
        expect(localStorage.getItem('tandem_refresh')).toBeNull()
    })
})

describe('testRemoteConnection()', () => {
    it('sends both the url and key as query params and returns the parsed result', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ success: true, model_loaded: true }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { testRemoteConnection } = await import('./api')
        const result = await testRemoteConnection('http://192.168.1.50:9000', 'my-key')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/settings/test-remote?url=http%3A%2F%2F192.168.1.50%3A9000&key=my-key',
            expect.anything(),
        )
        expect(result).toEqual({ success: true, model_loaded: true })
    })

    it('throws with the server-provided detail message on failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 400, json: async () => ({ detail: 'Authentication failed — check the Remote Server API Key' }),
        }))

        const { testRemoteConnection } = await import('./api')
        await expect(testRemoteConnection('http://192.168.1.50:9000', 'wrong-key'))
            .rejects.toThrow('Authentication failed — check the Remote Server API Key')
    })

    it('requires a url', async () => {
        const { testRemoteConnection } = await import('./api')
        await expect(testRemoteConnection('')).rejects.toThrow('URL is required')
    })
})

describe('generateTranscriptionRemoteKey()', () => {
    it('POSTs to the generate endpoint and returns the key', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ key: 'freshly-generated-key' }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { generateTranscriptionRemoteKey } = await import('./api')
        const result = await generateTranscriptionRemoteKey()

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/settings/transcription-remote-key/generate',
            expect.objectContaining({ method: 'POST' }),
        )
        expect(result).toEqual({ key: 'freshly-generated-key' })
    })

    it('throws when the server rejects the request', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({}) }))

        const { generateTranscriptionRemoteKey } = await import('./api')
        await expect(generateTranscriptionRemoteKey()).rejects.toThrow('Failed to generate key')
    })
})
