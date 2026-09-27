import { useEffect, useId, useState } from 'react'
import { getSettings, testGoogleBooksKey, updateSettings } from '../api'
import '../pages/SystemPage.css'

const SECRET_PLACEHOLDER = '********'

/**
 * The Google Books API key (issue #739), used by the Match search and the
 * print page lookup. Stored encrypted on the server like the Hardcover token:
 * a saved key comes back masked, and saving the mask leaves it unchanged. A
 * key saved here wins over the server's GOOGLE_BOOKS_API_KEY.
 *
 * The tooltip says which Google API the key needs and how to restrict it: the
 * console names it "Books API", lists it only once it is enabled, and a
 * referrer restriction blocks the server's own calls - each of which has
 * tripped someone up.
 */
export default function GoogleBooksSettings() {
    const [key, setKey] = useState('')
    const [fromEnv, setFromEnv] = useState(false)
    const [saving, setSaving] = useState(false)
    const [msg, setMsg] = useState(null)
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)
    const [tipOpen, setTipOpen] = useState(false)
    const tipId = useId()

    useEffect(() => {
        let cancelled = false
        getSettings()
            .then(s => {
                if (cancelled) return
                setKey(s.google_books_api_key || '')
                setFromEnv(!!s.google_books_key_from_env)
            })
            .catch(() => {})
        return () => { cancelled = true }
    }, [])

    const save = async () => {
        setSaving(true); setMsg(null)
        try {
            await updateSettings({ google_books_api_key: key })
            setMsg({ type: 'success', text: 'Saved' })
        } catch {
            setMsg({ type: 'error', text: 'Failed to save' })
        } finally {
            setSaving(false)
        }
    }

    const test = async () => {
        setTesting(true); setTestResult(null)
        try {
            // The mask is not a key: an empty one tests the key already in use.
            await testGoogleBooksKey(key === SECRET_PLACEHOLDER ? '' : key)
            setTestResult('✅ Google accepted the key.')
        } catch (err) {
            setTestResult(`❌ ${err.message}`)
        } finally {
            setTesting(false)
        }
    }

    return (
        <>
            <p className="system-card-desc">
                Used by the Google Books provider in metadata Match and by Print Page Counts. Google refuses
                almost every lookup without a key; with one it allows about 1,000 a day.
            </p>
            {fromEnv && (
                <p className="system-form-hint">
                    The server already has a key from GOOGLE_BOOKS_API_KEY. A key saved here is used instead.
                </p>
            )}
            <div className="system-form-row">
                <div className="system-form-label google-books-label">
                    <label htmlFor="googleBooksKey">API Key</label>
                    <span className="info-tip">
                        <button
                            type="button"
                            className="info-tip-button"
                            aria-label="Which Google API does the key need?"
                            aria-expanded={tipOpen}
                            aria-describedby={tipOpen ? tipId : undefined}
                            onClick={() => setTipOpen(o => !o)}
                            onMouseEnter={() => setTipOpen(true)}
                            onMouseLeave={() => setTipOpen(false)}
                            onFocus={() => setTipOpen(true)}
                            onBlur={() => setTipOpen(false)}
                            onKeyDown={e => { if (e.key === 'Escape') setTipOpen(false) }}
                        >
                            ⓘ
                        </button>
                        {tipOpen && (
                            <span role="tooltip" id={tipId} className="info-tip-text">
                                The key needs one API: <strong>Books API</strong> (books.googleapis.com). In the
                                Google Cloud console, enable it first under APIs &amp; Services → Library; only
                                enabled APIs appear in a key's API restrictions list. Then restrict the key to
                                Books API. For application restrictions choose None or your server's IP address,
                                not HTTP referrers: Tandem calls Google from the server, so a referrer
                                restriction blocks every lookup.
                            </span>
                        )}
                    </span>
                </div>
                <div className="system-form-inline">
                    <input
                        id="googleBooksKey"
                        type="password"
                        className="input"
                        aria-label="Google Books API key"
                        value={key}
                        onChange={e => setKey(e.target.value)}
                        placeholder="Paste your Google Books API key"
                        style={{ flex: '1 1 240px' }}
                    />
                    <button className="btn btn-secondary" onClick={test} disabled={testing} style={{ whiteSpace: 'nowrap' }}>
                        {testing ? 'Testing…' : 'Test Key'}
                    </button>
                </div>
                {testResult && <p className="system-form-hint" style={{ marginTop: 6 }}>{testResult}</p>}
            </div>
            {msg && <div className={`alert alert-${msg.type}`} style={{ marginBottom: 12 }}>{msg.text}</div>}
            <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </>
    )
}
