import { useEffect, useState } from 'react'
import { getSettings, updateSettings } from '../api'

/**
 * The durable on/off switch for the update check (issue #463).
 *
 * The System page asks once (`UpdateCheckBanner`). This is where the answer can
 * be changed afterwards. Every save also records the question as answered, so
 * turning the check off never brings the prompt back.
 *
 * Saves on change, like a switch: there is one field, and a separate Save button
 * would be a second step for no gain.
 */
export default function UpdateCheckSettings() {
    const [enabled, setEnabled] = useState(false)
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState(null)

    useEffect(() => {
        let cancelled = false
        getSettings()
            .then(s => { if (!cancelled) setEnabled(!!s.update_check_enabled) })
            .catch(() => {})
        return () => { cancelled = true }
    }, [])

    const toggle = async (next) => {
        const previous = enabled
        setEnabled(next)
        setSaving(true)
        setError(null)
        try {
            await updateSettings({ update_check_enabled: next, update_check_prompted: true })
        } catch (err) {
            setEnabled(previous)
            setError(err.message || 'Failed to update settings')
        } finally {
            setSaving(false)
        }
    }

    return (
        <div>
            <div className="system-form-toggle">
                <input
                    type="checkbox"
                    id="updateCheckToggle"
                    checked={enabled}
                    disabled={saving}
                    onChange={e => toggle(e.target.checked)}
                />
                <div>
                    <label htmlFor="updateCheckToggle" className="system-form-toggle-label">
                        Check GitHub for new releases
                    </label>
                    <p className="system-form-toggle-hint">
                        Every few hours the server asks GitHub whether a newer Tandem release is
                        published, and the System page says so. GitHub sees this server&apos;s address;
                        nothing else is sent. Updating is always done by hand.
                    </p>
                </div>
            </div>
            {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
        </div>
    )
}
