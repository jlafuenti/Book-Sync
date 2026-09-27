import { useCallback, useEffect, useState } from 'react'
import { cancelPrintPageFill, getPrintPageFillStatus, startPrintPageFill } from '../api'

/**
 * Fill ebooks' print page counts from Google Books (issue #739).
 *
 * The reader's print pages use an ebook's Print pages field when the EPUB has
 * no page list of its own. The job runs on the server in the background, since
 * a whole library takes far longer than a request may; this card starts it,
 * polls its status while it runs, and says how it ended - including when
 * Google Books' daily limit stopped it part-way.
 */
export default function PrintPageFill({ canEdit, pollMs = 2000 }) {
    const [status, setStatus] = useState(null)
    const [error, setError] = useState(null)
    const [starting, setStarting] = useState(false)

    const refresh = useCallback(async () => {
        try {
            setStatus(await getPrintPageFillStatus())
        } catch (err) {
            setError(err.message)
        }
    }, [])

    useEffect(() => { refresh() }, [refresh])

    const running = !!status?.running
    useEffect(() => {
        if (!running) return undefined
        const timer = setInterval(refresh, pollMs)
        return () => clearInterval(timer)
    }, [running, pollMs, refresh])

    const start = async () => {
        setError(null)
        setStarting(true)
        try {
            await startPrintPageFill()
            await refresh()
        } catch (err) {
            setError(err.message)
        } finally {
            setStarting(false)
        }
    }

    const cancel = async () => {
        try {
            await cancelPrintPageFill()
            await refresh()
        } catch (err) {
            setError(err.message)
        }
    }

    const remaining = status?.remaining ?? 0
    const ran = !!status?.finished_at

    return (
        <div>
            <p className="system-card-desc">
                Looks up printed page counts on Google Books for ebooks that have none, so the reader can show
                print pages. Books with an ISBN are matched by it; others only when the title and author match
                exactly. Counts set by hand are never changed.
            </p>
            {status && !status.api_key_configured && (
                <p className="system-form-hint error">
                    No Google Books API key is set. Google refuses almost every lookup without one: set
                    GOOGLE_BOOKS_API_KEY on the server first.
                </p>
            )}
            {status && (
                <p className="system-form-hint">
                    {remaining > 0
                        ? `${remaining} ${remaining === 1 ? 'ebook has' : 'ebooks have'} no print page count and ${remaining === 1 ? 'has' : 'have'} not been looked up.`
                        : 'Every ebook has a print page count or has been looked up.'}
                </p>
            )}
            {running && (
                <p className="system-form-hint" role="status">
                    Looking up {status.current} of {status.total} · {status.found} found
                </p>
            )}
            {!running && ran && (
                <p className="system-form-hint" role="status">
                    {status.message} {status.found} found, {status.no_match} not found, {status.errors} error{status.errors === 1 ? '' : 's'}.
                </p>
            )}
            {error && <p className="system-form-hint error">{error}</p>}
            {canEdit && (
                <div className="system-form-inline" style={{ marginTop: 8 }}>
                    {running ? (
                        <button className="btn btn-secondary" onClick={cancel} disabled={status.cancel_requested}>
                            {status.cancel_requested ? 'Cancelling…' : 'Cancel'}
                        </button>
                    ) : (
                        <button className="btn btn-primary" onClick={start} disabled={starting || !status || remaining === 0}>
                            {starting ? 'Starting…' : 'Look up print page counts'}
                        </button>
                    )}
                </div>
            )}
        </div>
    )
}
