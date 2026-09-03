// Server timestamps → browser-local display (issue #216).
//
// `server/utils.py:utcnow()` is naive UTC and every timestamp column in the
// schema is a bare `DateTime`, so a response carries `2026-08-22T14:03:00`
// with no `Z`. `new Date()` reads a zone-less string with a time component as
// LOCAL time, which shifted every "Added" / "Uploaded" / "Synced at" label by
// the browser's UTC offset and made a fresh sync read "Just now" for hours in
// a west-of-UTC zone. Parse in ONE place: append a `Z` unless the string
// already carries a zone, then format with the browser's own locale.

const EM_DASH = '—'

// Trailing `Z`, `+hh:mm` / `-hh:mm`, or `+hhmm` / `-hhmm`.
const HAS_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i
// A date with no time component is already UTC per the ISO date-only rule.
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/**
 * Parse a server timestamp, treating a zone-less string as UTC.
 * @returns {Date|null} null for falsy or unparseable input.
 */
export function parseServerDate(value) {
    if (!value) return null
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
    if (typeof value !== 'string') return null

    const s = value.trim()
    if (!s) return null
    const iso = DATE_ONLY.test(s) || HAS_ZONE.test(s) ? s : `${s}Z`
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? null : d
}

/** Localised date, e.g. "8/22/2026". */
export function formatDate(value, options, fallback = EM_DASH) {
    const d = parseServerDate(value)
    return d ? d.toLocaleDateString(undefined, options) : fallback
}

/** Localised date + time, e.g. "8/22/2026, 9:03 AM". */
export function formatDateTime(value, options, fallback = EM_DASH) {
    const d = parseServerDate(value)
    return d ? d.toLocaleString(undefined, options) : fallback
}

/** Localised time only, e.g. "9:03 AM". */
export function formatTime(value, options, fallback = EM_DASH) {
    const d = parseServerDate(value)
    return d ? d.toLocaleTimeString(undefined, options) : fallback
}

/** "Just now" / "45s ago" / "5h ago" / "3d ago", then a plain date past a week. */
export function formatRelativeTime(value, fallback = EM_DASH) {
    const d = parseServerDate(value)
    if (!d) return fallback
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000)
    if (diffSec < 30) return 'Just now'
    if (diffSec < 60) return `${diffSec}s ago`
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
    if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`
    return d.toLocaleDateString(undefined)
}

/** Elapsed time between two server timestamps, e.g. "1h 2m 3s". */
export function formatDuration(startValue, endValue, fallback = EM_DASH) {
    const start = parseServerDate(startValue)
    const end = parseServerDate(endValue)
    if (!start || !end) return fallback
    const diffMs = end.getTime() - start.getTime()
    if (diffMs < 0) return fallback
    const totalSec = Math.floor(diffMs / 1000)
    const hours = Math.floor(totalSec / 3600)
    const mins = Math.floor((totalSec % 3600) / 60)
    const secs = totalSec % 60
    if (hours > 0) return `${hours}h ${mins}m ${secs}s`
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
}
