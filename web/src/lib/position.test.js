import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
    positionTarget, deviceMeta, conflictFrom, writePosition, keepalivePosition,
} from './position'

const { updatePositionMock, sendPositionKeepaliveMock, getDeviceIdMock, getDeviceNameMock } = vi.hoisted(() => ({
    updatePositionMock: vi.fn(),
    sendPositionKeepaliveMock: vi.fn(),
    getDeviceIdMock: vi.fn(),
    getDeviceNameMock: vi.fn(),
}))

vi.mock('../api', () => ({
    updatePosition: updatePositionMock,
    sendPositionKeepalive: sendPositionKeepaliveMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
}))

beforeEach(() => {
    updatePositionMock.mockReset().mockResolvedValue({})
    sendPositionKeepaliveMock.mockReset()
    getDeviceIdMock.mockReset().mockReturnValue('this-device')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Firefox')
})

// docs/position-sync-contract.md: a paired book has ONE canonical record shared
// by the reader and the player. Four files each carried their own copy of this
// rule with a different signature (issue #274).
describe('positionTarget', () => {
    it('sends a paired book to the pair scope, whatever the pair id is called', () => {
        expect(positionTarget({ pairId: 7 }, 'audiobook', 3)).toEqual(['pair', 7])
        expect(positionTarget({ pair_id: 7 }, 'ebook', 3)).toEqual(['pair', 7])
        expect(positionTarget({ book_pair_id: 7 }, 'ebook', 3)).toEqual(['pair', 7])
    })

    it('sends an unpaired book to its own media scope', () => {
        expect(positionTarget({ pair_id: null }, 'audiobook', 3)).toEqual(['audiobook', 3])
        expect(positionTarget(null, 'ebook', 3)).toEqual(['ebook', 3])
        expect(positionTarget(undefined, 'ebook', 3)).toEqual(['ebook', 3])
    })

    it('falls back to the book\'s own id when no media id is passed', () => {
        expect(positionTarget({ id: 12 }, 'audiobook')).toEqual(['audiobook', 12])
    })

    it('treats pair id 0 as no pair, not as pair zero', () => {
        // Ids are 1-based; a 0 here means "unset", and writing to `pair/0`
        // would 404 rather than land the position.
        expect(positionTarget({ pair_id: 0 }, 'ebook', 3)).toEqual(['ebook', 3])
    })
})

describe('deviceMeta', () => {
    it('is exactly the device triple the contract names', () => {
        expect(Object.keys(deviceMeta()).sort()).toEqual(['captured_at', 'device_id', 'device_name'])
        expect(deviceMeta().device_id).toBe('this-device')
        expect(deviceMeta().device_name).toBe('Web · Firefox')
    })

    it('reads captured_at fresh per call, so batched writes stamp their own moment', () => {
        vi.useFakeTimers()
        try {
            vi.setSystemTime(new Date('2026-01-01T00:00:00.000Z'))
            const first = deviceMeta().captured_at
            vi.setSystemTime(new Date('2026-01-01T00:00:05.000Z'))
            const second = deviceMeta().captured_at

            expect(first).toBe('2026-01-01T00:00:00.000Z')
            expect(second).toBe('2026-01-01T00:00:05.000Z')
        } finally {
            vi.useRealTimers()
        }
    })
})

// Issue #54: a rejection that just echoes this device's own id is this device's
// retried or out-of-order write bouncing off itself — not a conflict, and
// surfacing it would show the user a banner about their own writing.
describe('conflictFrom', () => {
    it('is null when the write was accepted', () => {
        expect(conflictFrom({ rejected: false, device_id: 'other-device' })).toBeNull()
        expect(conflictFrom(null)).toBeNull()
        expect(conflictFrom(undefined)).toBeNull()
    })

    it('is null when the rejection came from this very device', () => {
        expect(conflictFrom({ rejected: true, device_id: 'this-device' })).toBeNull()
    })

    it('is null when the rejection names no device at all', () => {
        expect(conflictFrom({ rejected: true })).toBeNull()
    })

    it('reports a foreign device, with the current epubjs_cfi hint', () => {
        expect(conflictFrom({
            rejected: true,
            device_id: 'phone',
            device_name: 'Pixel',
            audio_position_ms: 90_000,
            hints: [
                { kind: 'readium_locator', value: 'loc', current: true },
                { kind: 'epubjs_cfi', value: 'epubcfi(/6/4!/2)', current: true },
            ],
        })).toEqual({
            cfi: 'epubcfi(/6/4!/2)',
            deviceName: 'Pixel',
            audioPositionMs: 90_000,
        })
    })

    it('ignores a stale cfi hint — a hint is only usable while it is current', () => {
        const conflict = conflictFrom({
            rejected: true,
            device_id: 'phone',
            device_name: 'Pixel',
            hints: [{ kind: 'epubjs_cfi', value: 'epubcfi(/6/4!/2)', current: false }],
        })
        expect(conflict.cfi).toBeNull()
    })

    it('falls back to the device id when the other device is unnamed', () => {
        const conflict = conflictFrom({ rejected: true, device_id: 'phone' })
        expect(conflict).toEqual({ cfi: null, deviceName: 'phone', audioPositionMs: 0 })
    })
})

describe('writePosition', () => {
    it('goes through updatePosition with the device triple attached', async () => {
        await writePosition(['pair', 7], { epub_chapter: 4 })

        expect(updatePositionMock).toHaveBeenCalledTimes(1)
        const [scope, ident, payload] = updatePositionMock.mock.calls[0]
        expect(scope).toBe('pair')
        expect(ident).toBe(7)
        expect(payload.epub_chapter).toBe(4)
        expect(payload.device_id).toBe('this-device')
        expect(payload.device_name).toBe('Web · Firefox')
        expect(typeof payload.captured_at).toBe('string')
    })

    it('claims `source` only when the caller asks for it', async () => {
        await writePosition(['pair', 7], { audio_position_ms: 1 })
        expect('source' in updatePositionMock.mock.calls[0][2]).toBe(false)

        await writePosition(['pair', 7], { audio_position_ms: 1 }, { claimSource: 'audiobook' })
        expect(updatePositionMock.mock.calls[1][2].source).toBe('audiobook')
    })

    it('never lets a caller smuggle `source` in through the fields', async () => {
        // The contract's "who may claim source" rule is the option, and only
        // the option. A field named `source` in the payload would route round
        // it silently.
        await writePosition(['pair', 7], { source: 'ebook', audio_position_ms: 1 })
        expect('source' in updatePositionMock.mock.calls[0][2]).toBe(false)
    })

    it('lets a caller pin captured_at to the moment the position was taken', async () => {
        // EbookReader stamps the page turn, then awaits the sync-map match
        // before writing; the write must attest when the reader was there, not
        // when the round-trip finished.
        await writePosition(['ebook', 3], { captured_at: '2026-01-01T00:00:00.000Z' })
        expect(updatePositionMock.mock.calls[0][2].captured_at).toBe('2026-01-01T00:00:00.000Z')
    })

    it('returns the server result unchanged so the caller can adjudicate it', async () => {
        updatePositionMock.mockResolvedValue({ rejected: true, device_id: 'phone' })
        await expect(writePosition(['pair', 7], {})).resolves.toEqual({ rejected: true, device_id: 'phone' })
    })
})

describe('keepalivePosition', () => {
    it('builds the same payload but sends it through the keepalive transport', () => {
        keepalivePosition(['pair', 7], { audio_position_ms: 500, append_to_log: true },
            { claimSource: 'audiobook' })

        expect(updatePositionMock).not.toHaveBeenCalled()
        const [scope, ident, payload] = sendPositionKeepaliveMock.mock.calls[0]
        expect([scope, ident]).toEqual(['pair', 7])
        expect(payload).toMatchObject({
            source: 'audiobook',
            audio_position_ms: 500,
            append_to_log: true,
            device_id: 'this-device',
            device_name: 'Web · Firefox',
        })
    })
})
