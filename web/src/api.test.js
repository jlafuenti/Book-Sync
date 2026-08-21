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

describe('testHardcoverConnection()', () => {
    it('sends the token as a query param and returns the parsed result', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ success: true, username: 'jesse' }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { testHardcoverConnection } = await import('./api')
        const result = await testHardcoverConnection('my-token')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/settings/test-hardcover?token=my-token',
            expect.anything(),
        )
        expect(result).toEqual({ success: true, username: 'jesse' })
    })

    it('throws with the server-provided detail message on failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 400, json: async () => ({ detail: 'Authentication failed — check your API token' }),
        }))

        const { testHardcoverConnection } = await import('./api')
        await expect(testHardcoverConnection('bad')).rejects.toThrow('Authentication failed')
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

describe('repairChapterEncoding()', () => {
    it('POSTs to the repair endpoint for the given item and returns the result', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ status: 'repaired', detail: null, item_id: 1538 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { repairChapterEncoding } = await import('./api')
        const result = await repairChapterEncoding(1538)

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/troubleshoot/repair-chapter-encoding/1538',
            expect.objectContaining({ method: 'POST' }),
        )
        expect(result).toEqual({ status: 'repaired', detail: null, item_id: 1538 })
    })

    it('throws with the server-provided detail message on failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 404, json: async () => ({ detail: 'Audiobook not found' }),
        }))

        const { repairChapterEncoding } = await import('./api')
        await expect(repairChapterEncoding(999)).rejects.toThrow('Audiobook not found')
    })
})

describe('bulkRepairChapterEncoding()', () => {
    it('POSTs the item_ids list and returns repaired/failures', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ repaired: 1, failures: [{ item_id: 2, title: 'Book Two', error: 'ffmpeg not found' }] }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { bulkRepairChapterEncoding } = await import('./api')
        const result = await bulkRepairChapterEncoding([1, 2])

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/troubleshoot/bulk-repair-chapter-encoding',
            expect.objectContaining({ method: 'POST', body: JSON.stringify({ item_ids: [1, 2] }) }),
        )
        expect(result).toEqual({ repaired: 1, failures: [{ item_id: 2, title: 'Book Two', error: 'ffmpeg not found' }] })
    })

    it('throws when the server rejects the request', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { bulkRepairChapterEncoding } = await import('./api')
        await expect(bulkRepairChapterEncoding([1])).rejects.toThrow('Bulk repair failed')
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

    it('createBackup() POSTs the label', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ id: '2026-07-13_120000-manual', is_manual: true }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { createBackup } = await import('./api')
        const result = await createBackup('before reorg')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/stats/backups',
            expect.objectContaining({
                method: 'POST',
                body: JSON.stringify({ label: 'before reorg' }),
            }),
        )
        expect(result.is_manual).toBe(true)
    })

    it('deleteBackup() DELETEs the id', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ deleted: true, backup_id: '2026-07-13' }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { deleteBackup } = await import('./api')
        const result = await deleteBackup('2026-07-13')

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/stats/backups/2026-07-13',
            expect.objectContaining({ method: 'DELETE' }),
        )
        expect(result.deleted).toBe(true)
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

    it('percent-encodes the filename segment so a # is not read as a fragment', async () => {
        // Issue #126: without encoding, the browser treats everything from '#' on as
        // a URL fragment -- the ?token= never reaches the server and the cover 401s.
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ token: 'scoped-cover-token', expires_in: 900 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { coverSrc } = await import('./api')
        const src = await coverSrc('/api/files/covers/A_#1.jpg')

        // The token is still minted for the RAW filename -- that's what the server
        // compares the decoded path param against.
        expect(fetchMock).toHaveBeenCalledWith(
            expect.stringContaining('/api/auth/media-token?resource_type=cover&resource_id=A_%231.jpg'),
            expect.anything(),
        )
        expect(src).toBe('/api/files/covers/A_%231.jpg?token=scoped-cover-token')
    })

    it('encodes spaces alongside # for the production cover filename shape', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ token: 'scoped-cover-token', expires_in: 900 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { coverSrc } = await import('./api')
        const src = await coverSrc('/api/files/covers/James_Patterson - Private_#1_Suspect.jpg')

        expect(src).toBe(
            '/api/files/covers/James_Patterson%20-%20Private_%231_Suspect.jpg?token=scoped-cover-token',
        )
    })

    it('keeps an existing query string and still appends the token', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ token: 'scoped-cover-token', expires_in: 900 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { coverSrc } = await import('./api')
        const src = await coverSrc('/api/files/covers/book.jpg?v=2')

        expect(src).toBe('/api/files/covers/book.jpg?v=2&token=scoped-cover-token')
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

describe('getDeviceId()', () => {
    it('generates a UUID-shaped id and persists it under tandem_device_id', async () => {
        const { getDeviceId } = await import('./api')
        const id = getDeviceId()

        expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
        expect(localStorage.getItem('tandem_device_id')).toBe(id)
    })

    it('returns the same value across repeated calls once persisted in localStorage', async () => {
        const { getDeviceId } = await import('./api')
        const first = getDeviceId()
        const second = getDeviceId()

        expect(second).toBe(first)
    })

    it('reuses a pre-existing tandem_device_id from localStorage instead of generating a new one', async () => {
        localStorage.setItem('tandem_device_id', 'preexisting-device-id')

        const { getDeviceId } = await import('./api')
        expect(getDeviceId()).toBe('preexisting-device-id')
    })

    it('falls back to the hand-rolled v4 generator when crypto.randomUUID is unavailable', async () => {
        // Non-HTTPS contexts (and some older browsers) expose `crypto` without
        // `randomUUID` -- getDeviceId() must still produce a valid v4 UUID.
        vi.stubGlobal('crypto', {})

        const { getDeviceId } = await import('./api')
        const id = getDeviceId()

        expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
        expect(localStorage.getItem('tandem_device_id')).toBe(id)
    })
})

describe('getDeviceName()', () => {
    it('derives a friendly name from navigator.userAgent and persists it under tandem_device_name', async () => {
        vi.stubGlobal('navigator', {
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        })

        const { getDeviceName } = await import('./api')
        const name = getDeviceName()

        expect(name).toContain('Web')
        expect(name).toContain('Chrome')
        expect(localStorage.getItem('tandem_device_name')).toBe(name)
    })

    it('reuses a pre-existing tandem_device_name from localStorage instead of re-deriving it', async () => {
        localStorage.setItem('tandem_device_name', 'My Saved Name')

        const { getDeviceName } = await import('./api')
        expect(getDeviceName()).toBe('My Saved Name')
    })

    it('derives "Safari" for a Safari user agent (no Chrome token present)', async () => {
        vi.stubGlobal('navigator', {
            userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        })

        const { getDeviceName } = await import('./api')
        expect(getDeviceName()).toBe('Web · Safari')
    })
})

describe('resetPosition()', () => {
    it('DELETEs the scoped position endpoint', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ status: 'ok' }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { resetPosition } = await import('./api')
        const result = await resetPosition('ebook', 7)

        const [url, options] = fetchMock.mock.calls[0]
        expect(url).toContain('/sync/position/ebook/7')
        expect(options.method).toBe('DELETE')
        expect(result).toEqual({ status: 'ok' })
    })

    it('throws on a server error', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { resetPosition } = await import('./api')
        await expect(resetPosition('ebook', 7)).rejects.toThrow('Failed to reset position')
    })
})

describe('getPosition()', () => {
    it('returns null on a 204 ("never opened"), distinct from a position at chapter 0', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))

        const { getPosition } = await import('./api')
        const result = await getPosition('pair', 42)

        expect(result).toBeNull()
    })

    it('fetches the scoped position endpoint and returns the parsed body on 200', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ epub_chapter: 3, anchor_revision: 2 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { getPosition } = await import('./api')
        const result = await getPosition('ebook', 7)

        expect(fetchMock).toHaveBeenCalledWith('/api/sync/position/ebook/7', expect.anything())
        expect(result).toEqual({ epub_chapter: 3, anchor_revision: 2 })
    })

    it('throws on a genuine server error', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

        const { getPosition } = await import('./api')
        await expect(getPosition('pair', 42)).rejects.toThrow('Failed to fetch position')
    })
})

describe('updatePosition()', () => {
    it('PUTs the whole position payload to the scoped endpoint', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ epub_chapter: 3 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { updatePosition } = await import('./api')
        const result = await updatePosition('pair', 42, { epub_chapter: 3, source: 'ebook' })

        expect(fetchMock).toHaveBeenCalledWith('/api/sync/position/pair/42', expect.objectContaining({
            method: 'PUT',
            body: JSON.stringify({ epub_chapter: 3, source: 'ebook' }),
        }))
        expect(result).toEqual({ epub_chapter: 3 })
    })

    it('returns { rejected: true, ...serverState } on 409 (a newer write already applied)', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 409, json: async () => ({ epub_chapter: 9, device_name: 'Phone' }),
        }))

        const { updatePosition } = await import('./api')
        const result = await updatePosition('pair', 42, { epub_chapter: 3 })

        expect(result).toEqual({ epub_chapter: 9, device_name: 'Phone', rejected: true })
    })

    it('still throws on a genuine server error (not 409)', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { updatePosition } = await import('./api')
        await expect(updatePosition('pair', 42, {})).rejects.toThrow('Failed to update position')
    })
})

describe('sendPositionKeepalive()', () => {
    // Fire-and-forget save used on tab close; must never throw into the
    // caller and must omit nothing the canonical PositionUpdate schema needs.

    it('does nothing without an access token', async () => {
        const fetchMock = vi.fn()
        vi.stubGlobal('fetch', fetchMock)

        const { sendPositionKeepalive } = await import('./api')
        sendPositionKeepalive('pair', 42, { epub_chapter: 3 })

        expect(fetchMock).not.toHaveBeenCalled()
    })

    it('fires a keepalive PUT carrying the bearer token when a token is present', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({ ok: true })
        vi.stubGlobal('fetch', fetchMock)

        const { sendPositionKeepalive } = await import('./api')
        sendPositionKeepalive('pair', 42, { epub_chapter: 3 })

        expect(fetchMock).toHaveBeenCalledWith('/api/sync/position/pair/42', expect.objectContaining({
            method: 'PUT',
            keepalive: true,
            headers: expect.objectContaining({ Authorization: 'Bearer access-1' }),
            body: JSON.stringify({ epub_chapter: 3 }),
        }))
    })

    it('swallows a synchronous fetch failure instead of throwing (there is no UI left to show it)', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn(() => { throw new Error('network unavailable') }))

        const { sendPositionKeepalive } = await import('./api')
        expect(() => sendPositionKeepalive('pair', 42, { epub_chapter: 3 })).not.toThrow()
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

describe('off-hours transcription window (issue #106)', () => {
    it('runQueueItemNow POSTs to the item run-now endpoint', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ id: 7, force_run: true }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { runQueueItemNow } = await import('./api')
        const result = await runQueueItemNow(7)

        expect(fetchMock).toHaveBeenCalledWith(
            '/api/transcription/queue/7/run-now',
            expect.objectContaining({ method: 'POST' }),
        )
        expect(result).toEqual({ id: 7, force_run: true })
    })

    it('runQueueItemNow surfaces the server-provided reason on failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 400, json: async () => ({ detail: 'Cannot run a cancelled item' }),
        }))

        const { runQueueItemNow } = await import('./api')
        await expect(runQueueItemNow(7)).rejects.toThrow('Cannot run a cancelled item')
    })

    it('getOffHoursStatus fetches the window state', async () => {
        const payload = { enabled: true, open: false, start: '01:00', end: '07:00', timezone: 'UTC' }
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => payload })
        vi.stubGlobal('fetch', fetchMock)

        const { getOffHoursStatus } = await import('./api')

        expect(await getOffHoursStatus()).toEqual(payload)
        expect(fetchMock).toHaveBeenCalledWith('/api/transcription/offhours', expect.anything())
    })

    it('getOffHoursStatus throws when the endpoint is unavailable', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { getOffHoursStatus } = await import('./api')
        await expect(getOffHoursStatus()).rejects.toThrow('Failed to fetch off-hours status')
    })

    it('updateSettings surfaces a validation detail instead of a generic message', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 400,
            json: async () => ({ detail: 'Off-hours window start and end must differ' }),
        }))

        const { updateSettings } = await import('./api')
        await expect(updateSettings({ transcription_offhours_start: '07:00' }))
            .rejects.toThrow('Off-hours window start and end must differ')
    })

    it('updateSettings returns the saved settings on success', async () => {
        const saved = { transcription_offhours_enabled: true }
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => saved }))

        const { updateSettings } = await import('./api')
        expect(await updateSettings(saved)).toEqual(saved)
    })
})

describe('getProgress()', () => {
    // The endpoint used to be get-or-create: reading a book you had never
    // opened INSERTed a row, and two clients doing it at once left two — after
    // which that one book 500ed forever (issue #64). It is read-only now and
    // answers 204 when there is nothing to report.
    it('resolves to null on 204 instead of trying to parse an empty body', async () => {
        const json = vi.fn()
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204, json }))

        const { getProgress } = await import('./api')
        expect(await getProgress('ebook', 7)).toBeNull()
        expect(json).not.toHaveBeenCalled()
    })

    it('returns the parsed progress when there is one', async () => {
        const progress = { id: 3, epub_chapter: 4, audio_position_ms: 1000 }
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => progress,
        }))

        const { getProgress } = await import('./api')
        expect(await getProgress('ebook', 7)).toEqual(progress)
    })

    it('still throws on a real failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { getProgress } = await import('./api')
        await expect(getProgress('ebook', 7)).rejects.toThrow('Failed to fetch progress')
    })
})

// Issue #48: the library list endpoints are paginated ({items,total,page,limit}).
// The `*Page` functions expose one page; the legacy `getEbooks()`-style
// functions keep returning the full array by walking every page, so the pages
// that still need whole lists (Library, Pairs, Series, Home) are untouched.
describe('paginated library lists (issue #48)', () => {
    function pageResponse(items, total, page, limit) {
        return { ok: true, status: 200, json: async () => ({ items, total, page, limit }) }
    }

    it('getEbooksPage builds page/limit/q into the query string and returns the envelope', async () => {
        const fetchMock = vi.fn().mockResolvedValue(pageResponse([{ id: 1 }], 1, 2, 25))
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getEbooksPage } = await import('./api')
        const body = await getEbooksPage({ page: 2, limit: 25, q: 'dune messiah' })

        expect(fetchMock.mock.calls[0][0]).toBe('/api/library/ebooks?page=2&limit=25&q=dune+messiah')
        expect(body).toEqual({ items: [{ id: 1 }], total: 1, page: 2, limit: 25 })
    })

    it('getAudiobooksPage / getPairsPage / getNewPairsPage hit their endpoints and omit an empty q', async () => {
        const fetchMock = vi.fn().mockResolvedValue(pageResponse([], 0, 1, 100))
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getAudiobooksPage, getPairsPage, getNewPairsPage } = await import('./api')
        await getAudiobooksPage()
        await getPairsPage({ page: 3 })
        await getNewPairsPage({ limit: 10 })

        expect(fetchMock.mock.calls.map(c => c[0])).toEqual([
            '/api/library/audiobooks?page=1&limit=100',
            '/api/library/pairs?page=3&limit=100',
            '/api/library/new-pairs?page=1&limit=10',
        ])
    })

    it('getEbooks() walks every page and returns the concatenated array', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(pageResponse([{ id: 1 }, { id: 2 }], 3, 1, 2))
            .mockResolvedValueOnce(pageResponse([{ id: 3 }], 3, 2, 2))
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { fetchAllPages } = await import('./api')
        // Drive the walker with a 2-item page size so the test stays small.
        const all = await fetchAllPages(
            (page) => import('./api').then(m => m.getEbooksPage({ page, limit: 2 })))

        expect(all.map(e => e.id)).toEqual([1, 2, 3])
        expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('getPairs() returns an array (one full page means one request)', async () => {
        const fetchMock = vi.fn().mockResolvedValue(pageResponse([{ id: 9 }], 1, 1, 500))
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getPairs } = await import('./api')
        const pairs = await getPairs()

        expect(Array.isArray(pairs)).toBe(true)
        expect(pairs).toEqual([{ id: 9 }])
        expect(fetchMock).toHaveBeenCalledTimes(1)
        expect(fetchMock.mock.calls[0][0]).toBe('/api/library/pairs?page=1&limit=500')
    })

    it('getNewItems() keeps the {ebooks, audiobooks} array shape, walking both sub-lists', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce({
                ok: true, status: 200, json: async () => ({
                    ebooks: { items: [{ id: 1 }], total: 1, page: 1, limit: 500 },
                    audiobooks: { items: [{ id: 7 }], total: 1, page: 1, limit: 500 },
                }),
            })
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getNewItems } = await import('./api')
        const body = await getNewItems()

        expect(body).toEqual({ ebooks: [{ id: 1 }], audiobooks: [{ id: 7 }] })
    })

    it('fetchAllPages stops when a page comes back short, and never loops on an empty page', async () => {
        const { fetchAllPages } = await import('./api')
        const calls = []
        const all = await fetchAllPages(async (page) => {
            calls.push(page)
            return { items: [], total: 0, page, limit: 500 }
        })
        expect(all).toEqual([])
        expect(calls).toEqual([1])
    })
})

describe('whole-list wrappers and multi-file folder actions', () => {
    it('getEbooks / getAudiobooks / getNewPairs walk the paged endpoints and return arrays', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ items: [{ id: 3 }], total: 1, page: 1, limit: 500 }),
        })
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getEbooks, getAudiobooks, getNewPairs } = await import('./api')
        expect(await getEbooks()).toEqual([{ id: 3 }])
        expect(await getAudiobooks()).toEqual([{ id: 3 }])
        expect(await getNewPairs()).toEqual([{ id: 3 }])

        expect(fetchMock.mock.calls.map(c => c[0])).toEqual([
            '/api/library/ebooks?page=1&limit=500',
            '/api/library/audiobooks?page=1&limit=500',
            '/api/library/new-pairs?page=1&limit=500',
        ])
    })

    it('a failed page rejects instead of returning a partial list', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))
        localStorage.setItem('tandem_token', 't')
        const { getEbooks } = await import('./api')
        await expect(getEbooks()).rejects.toThrow('Failed to fetch ebooks')
    })

    it('dismissMultiFileFolder and removeMultiFileTracks POST to the troubleshoot endpoints', async () => {
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ deleted: 2 }) })
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { dismissMultiFileFolder, removeMultiFileTracks } = await import('./api')
        await dismissMultiFileFolder(7)
        const res = await removeMultiFileTracks(7)

        expect(res).toEqual({ deleted: 2 })
        expect(fetchMock.mock.calls[0][0]).toBe('/api/troubleshoot/multi-file/7/dismiss')
        expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST' }))
        expect(fetchMock.mock.calls[1][0]).toBe('/api/troubleshoot/multi-file/7/remove-tracks')
    })
})

// Issue #120: the Library page is server-driven. `/library/items` is the mixed
// list (pairs + unpaired media, or a tab's slice of it) filtered, sorted and
// paged on the server; `/library/facets` is the pill options + tab counts.
describe('library browse (issue #120)', () => {
    function ok(body) {
        return { ok: true, status: 200, json: async () => body }
    }

    it('getLibraryItemsPage builds every filter into the query string, omitting empties', async () => {
        const fetchMock = vi.fn().mockResolvedValue(ok({ items: [], total: 0, page: 2, limit: 50 }))
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getLibraryItemsPage } = await import('./api')
        await getLibraryItemsPage({
            tab: 'unpaired', kind: 'ebook', q: 'dune', author: 'Frank Herbert', series: '',
            sort: 'author', dir: 'desc', page: 2, limit: 50,
        })
        await getLibraryItemsPage()

        expect(fetchMock.mock.calls[0][0]).toBe(
            '/api/library/items?page=2&limit=50&q=dune&tab=unpaired&kind=ebook&author=Frank+Herbert&sort=author&dir=desc')
        expect(fetchMock.mock.calls[1][0]).toBe('/api/library/items?page=1&limit=100')
    })

    it('getLibraryItemsPage returns the envelope and throws on a non-ok response', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(ok({ items: [{ kind: 'pair' }], total: 1, page: 1, limit: 100 }))
            .mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) })
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getLibraryItemsPage } = await import('./api')
        expect(await getLibraryItemsPage()).toEqual({ items: [{ kind: 'pair' }], total: 1, page: 1, limit: 100 })
        await expect(getLibraryItemsPage()).rejects.toThrow('Failed to fetch library items')
    })

    it('getUnpairedMedia walks tab=unpaired per kind and returns plain media objects', async () => {
        const fetchMock = vi.fn().mockImplementation(async (url) => {
            const u = new URL(url, 'http://x')
            const kind = u.searchParams.get('kind')
            const page = Number(u.searchParams.get('page'))
            const items = kind === 'ebook'
                ? (page === 1 ? [{ kind: 'ebook', ebook: { id: 1 } }, { kind: 'ebook', ebook: { id: 2 } }] : [{ kind: 'ebook', ebook: { id: 3 } }])
                : [{ kind: 'audiobook', audiobook: { id: 9 } }]
            const total = kind === 'ebook' ? 3 : 1
            return ok({ items, total, page, limit: kind === 'ebook' ? 2 : 500 })
        })
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getUnpairedMedia } = await import('./api')
        const { ebooks, audiobooks } = await getUnpairedMedia()

        expect(ebooks.map((e) => e.id)).toEqual([1, 2, 3])
        expect(audiobooks.map((a) => a.id)).toEqual([9])
        const urls = fetchMock.mock.calls.map((c) => c[0])
        expect(urls).toContain('/api/library/items?page=1&limit=500&tab=unpaired&kind=ebook')
        expect(urls).toContain('/api/library/items?page=1&limit=500&tab=unpaired&kind=audiobook')
        // Only the unpaired set is fetched — no /library/ebooks or /library/pairs walk.
        expect(urls.every((u) => u.startsWith('/api/library/items?'))).toBe(true)
    })

    it('getLibraryFacets scopes to tab/kind and returns authors, series and counts', async () => {
        const facets = { authors: [{ name: 'A', count: 2 }], series: [], counts: { ebooks: 4 } }
        const fetchMock = vi.fn().mockResolvedValue(ok(facets))
        vi.stubGlobal('fetch', fetchMock)
        localStorage.setItem('tandem_token', 't')

        const { getLibraryFacets } = await import('./api')
        expect(await getLibraryFacets({ tab: 'new', kind: 'pair' })).toEqual(facets)
        await getLibraryFacets()

        expect(fetchMock.mock.calls[0][0]).toBe('/api/library/facets?tab=new&kind=pair')
        expect(fetchMock.mock.calls[1][0]).toBe('/api/library/facets')
    })
})
