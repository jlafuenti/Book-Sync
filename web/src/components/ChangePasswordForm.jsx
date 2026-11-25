import React, { useState } from 'react'
import { changePassword } from '../api'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, passwordError } from '../lib/passwordPolicy'

/**
 * Current, new and confirm-new password, checked here and then sent to
 * `POST /api/auth/change-password`.
 *
 * Shared by the forced-reset screen (`ChangePasswordPage`) and the voluntary
 * form on the Account page (issue #962), so the two cannot drift on the rules
 * the way the web, Android and the API once did (issue #205).
 *
 * A success has already signed this browser out: `changePassword()` clears the
 * tokens, because the server revoked every session. `onChanged` is only told
 * that it happened; the caller decides where the user goes next.
 *
 * `confirmMessage`, when given, is asked with `window.confirm` once the fields
 * pass and before the request goes out — the same pattern as
 * `SignOutEverywhere`, for the same reason: it ends every session the account
 * has.
 */
export default function ChangePasswordForm({
    onChanged,
    submitLabel,
    busyLabel = 'Changing…',
    confirmMessage,
    idPrefix = 'change',
    children,
}) {
    const [oldPassword, setOldPassword] = useState('')
    const [newPassword, setNewPassword] = useState('')
    const [confirmPassword, setConfirmPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        if (loading) return
        setError('')

        if (newPassword !== confirmPassword) {
            setError('New passwords do not match')
            return
        }
        // Issue #205: the same 8..128 bound the server enforces, checked here
        // so the user sees it without a round trip.
        const policyError = passwordError(newPassword)
        if (policyError) {
            setError(policyError)
            return
        }
        if (confirmMessage && !window.confirm(confirmMessage)) return

        setLoading(true)
        try {
            await changePassword(oldPassword, newPassword)
        } catch (err) {
            // 400 (wrong current password) and 429 (locked out after repeated
            // failures) need different things from the user; the server's own
            // sentence is what tells them apart.
            setError(err.message)
            setLoading(false)
            return
        }
        onChanged()
    }

    return (
        <form onSubmit={handleSubmit}>
            {error && <div className="alert alert-error">⚠️ {error}</div>}

            <div className="form-group">
                <label htmlFor={`${idPrefix}-old-password`}>Current password</label>
                <input
                    id={`${idPrefix}-old-password`}
                    type="password"
                    className="form-input"
                    autoComplete="current-password"
                    value={oldPassword}
                    onChange={e => setOldPassword(e.target.value)}
                    required
                />
            </div>
            <div className="form-group">
                <label htmlFor={`${idPrefix}-new-password`}>New password</label>
                <input
                    id={`${idPrefix}-new-password`}
                    type="password"
                    className="form-input"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={e => setNewPassword(e.target.value)}
                    required
                />
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '6px' }}>
                    {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters.
                </p>
            </div>
            <div className="form-group">
                <label htmlFor={`${idPrefix}-confirm-password`}>Confirm new password</label>
                <input
                    id={`${idPrefix}-confirm-password`}
                    type="password"
                    className="form-input"
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={e => setConfirmPassword(e.target.value)}
                    required
                />
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button type="submit" className="btn btn-primary" disabled={loading}>
                    {loading ? busyLabel : submitLabel}
                </button>
                {children}
            </div>
        </form>
    )
}
