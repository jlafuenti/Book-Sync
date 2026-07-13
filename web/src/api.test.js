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

describe('backups', () => {
    it('getBackupStatus() fetches the status endpoint', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ configured: true, stale: false }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { getBackupStatus } = await import('./api')
        const result = await getBackupStatus()

        expect(fetchMock).toHaveBeenCalledWith('/api/stats/backup', expect.anything())
        expect(result).toEqual({ configured: true, stale: false })
    })

    it('listBackups() fetches the list endpoint', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ location: '/backups', items: [] }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { listBackups } = await import('./api')
        const result = await listBackups()

        expect(fetchMock).toHaveBeenCalledWith('/api/stats/backups', expect.anything())
        expect(result).toEqual({ location: '/backups', items: [] })
    })

    it('restoreBackup() POSTs the id with confirm:true', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ restored: true, backup_id: '2026-07-13', covers_restored: true }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { restoreBackup } = await import('./api')
        const result = await restoreBackup('2026-07-13')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/stats/restore',
            expect.objectContaining({
                method: 'POST',
                body: JSON.stringify({ backup_id: '2026-07-13', confirm: true }),
            }),
        )
        expect(result.restored).toBe(true)
    })

    it('restoreBackup() throws the server detail on failure', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 500, json: async () => ({ detail: 'Restore failed: pg_restore exited 1' }),
        }))

        const { restoreBackup } = await import('./api')
        await expect(restoreBackup('2026-07-13')).rejects.toThrow('Restore failed: pg_restore exited 1')
    })
})

describe('coverSrc()', () => {
    it('mints a media token for the cover filename and appends it as ?token=', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ token: 'scoped-cover-token', expires_in: 900 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { coverSrc } = await import('./api')
        const src = await coverSrc('/api/files/covers/book.jpg')

        expect(fetchMock).toHaveBeenCalledWith(
            expect.stringContaining('/api/auth/media-token?resource_type=cover&resource_id=book.jpg'),
            expect.anything(),
        )
        expect(src).toBe('/api/files/covers/book.jpg?token=scoped-cover-token')
    })

    it('returns falsy paths unchanged without minting a token', async () => {
        const fetchMock = vi.fn()
        vi.stubGlobal('fetch', fetchMock)

        const { coverSrc } = await import('./api')
        expect(await coverSrc(null)).toBeNull()
        expect(fetchMock).not.toHaveBeenCalled()
    })

    it('caches the token for a resource so a second call within TTL does not refetch', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ token: 'scoped-cover-token', expires_in: 900 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { coverSrc } = await import('./api')
        await coverSrc('/api/files/covers/book.jpg')
        await coverSrc('/api/files/covers/book.jpg')

        expect(fetchMock).toHaveBeenCalledTimes(1)
    })
})

describe('getAudiobookStreamUrl()', () => {
    it('mints a media token for the audiobook id and appends it as ?token=', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ token: 'scoped-audio-token', expires_in: 900 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { getAudiobookStreamUrl } = await import('./api')
        const url = await getAudiobookStreamUrl(42)

        expect(fetchMock).toHaveBeenCalledWith(
            expect.stringContaining('/api/auth/media-token?resource_type=audiobook&resource_id=42'),
            expect.anything(),
        )
        expect(url).toBe('/api/files/audiobook/42?token=scoped-audio-token')
    })
})

describe('prefetchMediaTokens()', () => {
    it('batch-mints tokens and warms the cache so coverSrc() does not refetch', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({
                tokens: { 'cover:a.jpg': 'tok-a', 'cover:b.jpg': 'tok-b' },
                expires_in: 900,
            }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { prefetchMediaTokens, coverSrc } = await import('./api')
        await prefetchMediaTokens([
            { resourceType: 'cover', resourceId: 'a.jpg' },
            { resourceType: 'cover', resourceId: 'b.jpg' },
        ])

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/auth/media-token/batch',
            expect.objectContaining({ method: 'POST' }),
        )

        const src = await coverSrc('/api/files/covers/a.jpg')
        expect(src).toBe('/api/files/covers/a.jpg?token=tok-a')
        // Only the one batch call -- coverSrc() found the token already cached.
        expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    it('does nothing for an empty resource list', async () => {
        const fetchMock = vi.fn()
        vi.stubGlobal('fetch', fetchMock)

        const { prefetchMediaTokens } = await import('./api')
        await prefetchMediaTokens([])

        expect(fetchMock).not.toHaveBeenCalled()
    })

    it('silently no-ops when the batch request fails', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

        const { prefetchMediaTokens } = await import('./api')
        await expect(prefetchMediaTokens([{ resourceType: 'cover', resourceId: 'a.jpg' }])).resolves.toBeUndefined()
    })
})

describe('testAbsConnection()', () => {
    it('sends the url and token as query params and returns the parsed result', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ success: true, book_libraries: ['Audiobooks'] }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { testAbsConnection } = await import('./api')
        const result = await testAbsConnection('http://192.168.1.60:13378', 'my-token')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/settings/test-abs?url=http%3A%2F%2F192.168.1.60%3A13378&token=my-token',
            expect.anything(),
        )
        expect(result).toEqual({ success: true, book_libraries: ['Audiobooks'] })
    })

    it('defaults to an empty token so the backend falls back to the saved credential', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ success: true }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { testAbsConnection } = await import('./api')
        await testAbsConnection('http://192.168.1.60:13378')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/settings/test-abs?url=http%3A%2F%2F192.168.1.60%3A13378&token=',
            expect.anything(),
        )
    })

    it('requires a url', async () => {
        const { testAbsConnection } = await import('./api')
        await expect(testAbsConnection('')).rejects.toThrow('URL is required')
    })

    it('throws with the server-provided detail message on failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 400, json: async () => ({ detail: 'Authentication failed — check your API token' }),
        }))

        const { testAbsConnection } = await import('./api')
        await expect(testAbsConnection('http://192.168.1.60:13378', 'wrong')).rejects.toThrow(
            'Authentication failed — check your API token'
        )
    })
})
