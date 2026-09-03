import { describe, it, expect, vi, afterEach } from 'vitest'
import {
    parseServerDate, formatDate, formatDateTime, formatTime, formatRelativeTime, formatDuration,
} from './datetime'

// Issue #216: every timestamp column on the server is a bare `DateTime` and
// `utcnow()` is naive, so responses carry `2026-08-22T14:03:00` with no zone.
// `new Date()` reads that as LOCAL time, which shifted every "Added" /
// "Uploaded" / "Synced at" label by the browser's UTC offset, and made
// Import Sources say "Just now" for hours west of UTC.

describe('parseServerDate', () => {
    it('treats a zone-less server timestamp as UTC', () => {
        expect(parseServerDate('2026-08-22T14:03:00').getTime())
            .toBe(Date.UTC(2026, 7, 22, 14, 3, 0))
    })

    it('keeps sub-second precision on a zone-less timestamp', () => {
        expect(parseServerDate('2026-08-22T14:03:00.123456').getTime())
            .toBe(Date.UTC(2026, 7, 22, 14, 3, 0, 123))
    })

    it('leaves a Z-suffixed timestamp alone', () => {
        expect(parseServerDate('2026-08-22T14:03:00Z').getTime())
            .toBe(Date.UTC(2026, 7, 22, 14, 3, 0))
    })

    it('leaves an offset-bearing timestamp alone (+hh:mm and +hhmm)', () => {
        expect(parseServerDate('2026-08-22T10:03:00-04:00').getTime())
            .toBe(Date.UTC(2026, 7, 22, 14, 3, 0))
        expect(parseServerDate('2026-08-22T10:03:00-0400').getTime())
            .toBe(Date.UTC(2026, 7, 22, 14, 3, 0))
    })

    it('returns null for falsy or unparseable input, and passes a Date through', () => {
        expect(parseServerDate(null)).toBeNull()
        expect(parseServerDate(undefined)).toBeNull()
        expect(parseServerDate('')).toBeNull()
        expect(parseServerDate('not a date')).toBeNull()
        const d = new Date(Date.UTC(2026, 7, 22))
        expect(parseServerDate(d)).toBe(d)
    })
})

describe('format helpers', () => {
    it('format the UTC instant, not the local-parsed one', () => {
        const utc = new Date(Date.UTC(2026, 7, 22, 14, 3, 0))
        expect(formatDate('2026-08-22T14:03:00')).toBe(utc.toLocaleDateString(undefined))
        expect(formatDateTime('2026-08-22T14:03:00')).toBe(utc.toLocaleString(undefined))
    })

    it('pass Intl options through', () => {
        const opts = { year: 'numeric', month: 'short', day: 'numeric' }
        const utc = new Date(Date.UTC(2026, 7, 22, 14, 3, 0))
        expect(formatDate('2026-08-22T14:03:00', opts)).toBe(utc.toLocaleDateString(undefined, opts))
    })

    it('format the time of day off the same UTC instant', () => {
        const utc = new Date(Date.UTC(2026, 7, 22, 14, 3, 0))
        const opts = { hour: 'numeric', minute: '2-digit' }
        expect(formatTime('2026-08-22T14:03:00', opts)).toBe(utc.toLocaleTimeString(undefined, opts))
    })

    it('fall back to an em dash for missing values', () => {
        expect(formatDate(null)).toBe('—')
        expect(formatDateTime(undefined)).toBe('—')
        expect(formatTime(null)).toBe('—')
        expect(formatDate('', {}, '')).toBe('')
        expect(formatDateTime(null, {}, 'never')).toBe('never')
    })
})

describe('formatDuration', () => {
    it('measures the span between two naive server timestamps', () => {
        expect(formatDuration('2026-08-22T14:03:00', '2026-08-22T14:03:07')).toBe('7s')
        expect(formatDuration('2026-08-22T14:03:00', '2026-08-22T14:05:07')).toBe('2m 7s')
        expect(formatDuration('2026-08-22T14:03:00', '2026-08-22T15:05:07')).toBe('1h 2m 7s')
    })

    it('falls back when either end is missing or the span is negative', () => {
        expect(formatDuration(null, '2026-08-22T14:03:00')).toBe('—')
        expect(formatDuration('2026-08-22T14:03:00', null)).toBe('—')
        expect(formatDuration('2026-08-22T14:03:00', '2026-08-22T14:02:00')).toBe('—')
    })
})

describe('formatRelativeTime', () => {
    afterEach(() => vi.useRealTimers())

    const at = (utcMs) => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date(utcMs))
    }
    const NOW = Date.UTC(2026, 7, 22, 14, 3, 0)

    it('reads a naive server timestamp one minute in the past as "1m ago"', () => {
        at(NOW)
        expect(formatRelativeTime('2026-08-22T14:02:00')).toBe('1m ago')
    })

    it('does not report a fresh sync as "Just now" for hours (the west-of-UTC bug)', () => {
        at(NOW)
        expect(formatRelativeTime('2026-08-22T09:03:00')).toBe('5h ago')
        expect(formatRelativeTime('2026-08-22T14:02:55')).toBe('Just now')
        expect(formatRelativeTime('2026-08-22T14:02:15')).toBe('45s ago')
        expect(formatRelativeTime('2026-08-19T14:03:00')).toBe('3d ago')
    })

    it('falls back to a date beyond a week, and to a caption when unset', () => {
        at(NOW)
        expect(formatRelativeTime('2026-07-01T14:03:00'))
            .toBe(new Date(Date.UTC(2026, 6, 1, 14, 3, 0)).toLocaleDateString(undefined))
        expect(formatRelativeTime(null, 'Never synced')).toBe('Never synced')
        expect(formatRelativeTime(null)).toBe('—')
    })
})
