import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { deleteAccount } from '../api'

const CONFIRM_WORD = 'DELETE'

/**
 * Account settings, and the web half of self-service deletion (issue #146).
 *
 * Play requires both an in-app path and a public web link for any app that
 * offers account creation; the Android app has the first, this page and
 * `/account-deletion` are its web counterparts. The same account, so the same
 * control.
 *
 * Two gates before the request goes out. The password is the server's — it is
 * re-verified there, so a tab left open on a shared machine is not enough on its
 * own. Typing the word is this page's, and it is here because the alternative is
 * a single button in a settings list that destroys an account on one click.
 */
export default function AccountPage({ user, onAccountDeleted }) {
    const [open, setOpen] = useState(false)
    const [password, setPassword] = useState('')
    const [confirm, setConfirm] = useState('')
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)

    const ready = password.length > 0 && confirm === CONFIRM_WORD && !busy

    const handleDelete = async () => {
        if (!ready) return
        setBusy(true)
        setError('')
        try {
            await deleteAccount(password)
            // The tokens are already cleared by the API call; dropping the user
            // unmounts to the login form through React rather than a reload.
            onAccountDeleted?.()
        } catch (err) {
            // 403 (wrong password) and 409 (last active superadmin) need
            // completely different things from the user, and only the server's
            // sentence tells them apart.
            setError(err.message)
            setBusy(false)
        }
    }

    return (
        <div className="page-content">
            <div className="page-header">
                <h2>Account</h2>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 24 }}>
                <div style={{ fontWeight: 600 }}>{user?.username}</div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                    {user?.email}
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: 4 }}>
                    Role: {user?.role || 'user'}
                </div>
            </div>

            <h2 style={{ fontSize: '1rem', color: 'var(--error)' }}>
                Danger zone
            </h2>
            <div className="card" style={{ padding: 16 }}>
                <p style={{ fontSize: '0.9rem', marginTop: 0 }}>
                    Permanently delete your account, bookmarks and reading positions from
                    this server. The books themselves belong to whoever runs the server
                    and are not affected.
                </p>

                {!open && (
                    <button
                        className="btn btn-danger"
                        onClick={() => setOpen(true)}
                    >
                        Delete account
                    </button>
                )}

                {open && (
                    <div>
                        <div className="form-group">
                            <label htmlFor="delete-password">Current password</label>
                            <input
                                id="delete-password"
                                type="password"
                                className="form-input"
                                autoComplete="current-password"
                                value={password}
                                onChange={e => { setPassword(e.target.value); setError('') }}
                            />
                        </div>
                        <div className="form-group">
                            <label htmlFor="delete-confirm">
                                Type {CONFIRM_WORD} to confirm
                            </label>
                            <input
                                id="delete-confirm"
                                type="text"
                                className="form-input"
                                autoComplete="off"
                                value={confirm}
                                onChange={e => setConfirm(e.target.value)}
                            />
                        </div>

                        {error && <div className="alert alert-error">{error}</div>}

                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <button
                                className="btn btn-danger"
                                data-testid="confirm-delete-account"
                                disabled={!ready}
                                onClick={handleDelete}
                            >
                                {busy ? 'Deleting…' : 'Delete account permanently'}
                            </button>
                            <button
                                className="btn btn-secondary"
                                disabled={busy}
                                onClick={() => {
                                    setOpen(false)
                                    setPassword('')
                                    setConfirm('')
                                    setError('')
                                }}
                            >
                                Cancel
                            </button>
                        </div>

                        <p style={{ marginTop: 12, fontSize: '0.85rem' }}>
                            <Link to="/account-deletion">
                                How account deletion works
                            </Link>
                        </p>
                    </div>
                )}
            </div>
        </div>
    )
}
