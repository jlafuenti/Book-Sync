import { useCallback, useEffect, useRef, useState } from 'react'
import { getWordTimingStatus, queueWordTiming } from '../api'
import Modal from './Modal'

/**
 * System page card: how many transcripts carry word-level timing, and a way to
 * queue the rest for re-transcription (issue #835). Admin-only; the page gates
 * it, the server enforces it.
 *
 * Transcripts made before the worker returned word timing can only get it by
 * being transcribed again, which costs a full transcription per book, so the
 * button asks first and the server queues at low priority.
 */

const books = (n) => `${n} ${n === 1 ? 'book' : 'books'}`

export default function WordTimingCard() {
    const [status, setStatus] = useState(null)
    const [error, setError] = useState(null)
    const [notice, setNotice] = useState(null)
    const [hidden, setHidden] = useState(false)
    const [busy, setBusy] = useState(false)
    const [confirmOpen, setConfirmOpen] = useState(false)
    const mounted = useRef(true)

    useEffect(() => {
        mounted.current = true
        return () => { mounted.current = false }
    }, [])

    const refresh = useCallback(async () => {
        try {
            const next = await getWordTimingStatus()
            if (!mounted.current) return
            setStatus(next)
            setError(null)
        } catch (err) {
            if (!mounted.current) return
            // 404: a server that predates the endpoint. There is nothing to
            // show, so the card is simply absent.
            if (err.status === 404) setHidden(true)
            else setError(err.message)
        }
    }, [])

    useEffect(() => { refresh() }, [refresh])

    const queueRest = async () => {
        setConfirmOpen(false)
        setBusy(true)
        setError(null)
        setNotice(null)
        try {
            const result = await queueWordTiming()
            if (!mounted.current) return
            setNotice(`Queued ${books(result.queued)}.`)
            await refresh()
        } catch (err) {
            if (mounted.current) setError(err.message)
        } finally {
            if (mounted.current) setBusy(false)
        }
    }

    if (hidden || (!status && !error)) return null

    const withWords = status?.with_words ?? 0
    const withoutWords = status?.without_words ?? 0
    const queued = status?.queued ?? 0
    const toQueue = Math.max(withoutWords - queued, 0)

    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>Word timing</h3>
            </div>
            <div className="system-card-body">
                {error && <div className="alert alert-error" role="alert">{error}</div>}
                {notice && <div className="alert alert-info" role="status">{notice}</div>}

                {status && (
                    <>
                        {withoutWords > 0 ? (
                            <>
                                <p style={{ marginTop: 0 }}>
                                    {`${withWords} of ${withWords + withoutWords} transcripts carry word timing.`}
                                </p>
                                <p className="system-card-desc">
                                    Books transcribed before the worker returned it can only get it by being transcribed again.
                                </p>
                            </>
                        ) : (
                            <p className="system-card-desc" style={{ marginTop: 0 }}>Every transcript carries word timing.</p>
                        )}

                        {queued > 0 && <p>{`${queued} waiting in the queue.`}</p>}

                        {toQueue > 0 && (
                            <div style={{ marginBottom: 14 }}>
                                <button
                                    className="btn btn-primary"
                                    disabled={busy}
                                    onClick={() => setConfirmOpen(true)}
                                >
                                    Queue the rest for re-transcription
                                </button>
                            </div>
                        )}
                    </>
                )}
            </div>

            {confirmOpen && (
                <Modal onClose={() => setConfirmOpen(false)} labelledBy="word-timing-confirm-title">
                    <h3 id="word-timing-confirm-title" style={{ marginTop: 0 }}>
                        {`Queue ${books(toQueue)} for re-transcription?`}
                    </h3>
                    <p>
                        {`This queues ${books(toQueue)} behind everything already waiting. Each one costs a full transcription, `
                            + 'which takes hours per book, so a large library can take weeks.'}
                    </p>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                        <button className="btn btn-secondary" onClick={() => setConfirmOpen(false)}>Cancel</button>
                        <button className="btn btn-primary" onClick={queueRest}>Queue them</button>
                    </div>
                </Modal>
            )}
        </div>
    )
}
