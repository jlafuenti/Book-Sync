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

describe('updateBookmark()', () => {
    it('sends device_id, device_name, and captured_at in the payload', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ audio_position_ms: 1000 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { updateBookmark } = await import('./api')
        await updateBookmark(5, {
            source: 'audiobook',
            audio_position_ms: 1000,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: '2026-07-20T12:00:00.000Z',
        })

        const [, options] = fetchMock.mock.calls[0]
        const sentBody = JSON.parse(options.body)
        expect(sentBody).toMatchObject({
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: '2026-07-20T12:00:00.000Z',
        })
    })

    it('returns the parsed body unchanged (no rejected marker) on a normal 200', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ audio_position_ms: 1000 }),
        }))

        const { updateBookmark } = await import('./api')
        const result = await updateBookmark(5, { source: 'audiobook', audio_position_ms: 1000 })

        expect(result).toEqual({ audio_position_ms: 1000 })
        expect(result.rejected).toBeUndefined()
    })

    it('returns { rejected: true, ...serverState } on 409 instead of throwing', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 409, json: async () => ({ audio_position_ms: 5000, device_name: 'Phone' }),
        }))

        const { updateBookmark } = await import('./api')
        const result = await updateBookmark(5, { source: 'audiobook', audio_position_ms: 1000 })

        expect(result).toEqual({ audio_position_ms: 5000, device_name: 'Phone', rejected: true })
    })

    it('still throws on a genuine server error (not 409)', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { updateBookmark } = await import('./api')
        await expect(updateBookmark(5, { source: 'audiobook' })).rejects.toThrow('Failed to update bookmark')
    })
})

describe('updateProgress()', () => {
    it('sends device_id, device_name, and captured_at in the payload', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ audio_position_ms: 1000 }),
        })
        vi.stubGlobal('fetch', fetchMock)

        const { updateProgress } = await import('./api')
        await updateProgress('audiobook', 7, {
            audio_position_ms: 1000,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: '2026-07-20T12:00:00.000Z',
        })

        const [, options] = fetchMock.mock.calls[0]
        const sentBody = JSON.parse(options.body)
        expect(sentBody).toMatchObject({
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: '2026-07-20T12:00:00.000Z',
        })
    })

    it('returns { rejected: true, ...serverState } on 409 instead of throwing', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 409, json: async () => ({ audio_position_ms: 5000 }),
        }))

        const { updateProgress } = await import('./api')
        const result = await updateProgress('audiobook', 7, { audio_position_ms: 1000 })

        expect(result).toEqual({ audio_position_ms: 5000, rejected: true })
    })

    it('still throws on a genuine server error (not 409)', async () => {
        localStorage.setItem('tandem_token', 'access-1')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))

        const { updateProgress } = await import('./api')
        await expect(updateProgress('audiobook', 7, {})).rejects.toThrow('Failed to update progress')
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
