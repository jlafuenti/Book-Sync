import React, { useState, useEffect } from 'react'
import { login, register, getMe, getRegistrationMode } from '../api'

/**
 * Issue #210. The page used to offer "Request Access" unconditionally, so an
 * operator who had closed registration got a visitor filling in the whole form
 * and then a 403 that explained nothing.
 *
 * It asks the server first, through the unauthenticated `GET
 * /api/auth/registration`, and starts optimistically on `open`: the mode only
 * ever *removes* something from this screen, so the pessimistic default would
 * flash a form and take it away, and a failed lookup would hide the request path
 * from an entire deployment over a transient 502. `getRegistrationMode` swallows
 * its own errors and answers `open` for the same reason.
 */
const MODE_OPEN = 'open'
const MODE_INVITE = 'invite'
const MODE_CLOSED = 'closed'

function LoginPage({ onLogin }) {
    const [isRegistering, setIsRegistering] = useState(false)
    const [username, setUsername] = useState('')
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [inviteCode, setInviteCode] = useState('')
    const [mode, setMode] = useState(MODE_OPEN)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [loading, setLoading] = useState(false)

    useEffect(() => {
        let live = true
        getRegistrationMode()
            .then((m) => { if (live) setMode(m) })
            // `getRegistrationMode` already answers 'open' rather than throwing,
            // but this page must not be the thing that breaks if that ever
            // changes: an unhandled rejection here would take the only screen a
            // signed-out user has with it.
            .catch(() => {})
        return () => { live = false }
    }, [])

    const canRequestAccess = mode !== MODE_CLOSED

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setSuccess('')
        setLoading(true)

        try {
            if (isRegistering) {
                // The code is part of the request only when the server asked for
                // one. An `open` server sees exactly the body it always saw.
                const args = [username, email, password]
                if (mode === MODE_INVITE) args.push(inviteCode)
                const data = await register(...args)
                setSuccess(data.message || 'Access request submitted. An admin must approve your account before you can sign in.')
                setUsername('')
                setEmail('')
                setPassword('')
                setInviteCode('')
                setIsRegistering(false)
            } else {
                await login(username, password)
                const user = await getMe()
                onLogin(user)
            }
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    const switchMode = (e) => {
        e.preventDefault()
        setIsRegistering(!isRegistering)
        setError('')
        setSuccess('')
    }

    return (
        <div className="login-page">
            <div className="login-card">
                <h1>
                    <svg viewBox="0 0 24 24" width="32" height="32" style={{ verticalAlign: 'middle', marginRight: '8px', marginBottom: '2px' }}>
                        <path fill="var(--accent)" d="M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z"/>
                    </svg>
                    Tandem
                </h1>
                <p className="subtitle">
                    {isRegistering ? 'Request an account' : 'Sign in to your account'}
                </p>

                {error && <div className="alert alert-error">⚠️ {error}</div>}
                {success && <div className="alert alert-success">✓ {success}</div>}

                <form onSubmit={handleSubmit} action="#" method="POST">
                    <div className="form-group">
                        <label htmlFor="username">Username</label>
                        <input
                            id="username"
                            name="username"
                            type="text"
                            className="form-input"
                            placeholder="Enter username"
                            autoComplete="username"
                            value={username}
                            onChange={e => setUsername(e.target.value)}
                            required
                        />
                    </div>

                    {isRegistering && (
                        <div className="form-group">
                            <label htmlFor="email">Email</label>
                            <input
                                id="email"
                                name="email"
                                type="email"
                                className="form-input"
                                placeholder="Enter email"
                                autoComplete="email"
                                value={email}
                                onChange={e => setEmail(e.target.value)}
                                required
                            />
                        </div>
                    )}

                    {/* Invite mode only. Required here and required server-side:
                        the field is a courtesy, the refusal is the rule — and the
                        refusal is deliberately the same neutral 201 a good code
                        gets, so nothing here can be used to test codes. */}
                    {isRegistering && mode === MODE_INVITE && (
                        <div className="form-group">
                            <label htmlFor="invite-code">Invite code</label>
                            <input
                                id="invite-code"
                                name="invite-code"
                                type="text"
                                className="form-input"
                                placeholder="Paste the code your administrator sent you"
                                autoComplete="off"
                                value={inviteCode}
                                onChange={e => setInviteCode(e.target.value)}
                                required
                            />
                        </div>
                    )}

                    <div className="form-group">
                        <label htmlFor="password">Password</label>
                        <input
                            id="password"
                            name="password"
                            type="password"
                            className="form-input"
                            placeholder="Enter password"
                            autoComplete={isRegistering ? 'new-password' : 'current-password'}
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                        />
                    </div>

                    <button
                        type="submit"
                        className="btn btn-primary"
                        style={{ width: '100%', justifyContent: 'center', marginBottom: '16px' }}
                        disabled={loading}
                    >
                        {loading ? <div className="spinner"></div> : (isRegistering ? 'Request Access' : 'Sign In')}
                    </button>
                </form>

                {!isRegistering && (
                    /* Issue #204: there is no self-service reset. An admin resets a
                       user (Users -> Reset password); a locked-out superadmin is
                       recovered with the break-glass CLI in docs/operations.md. */
                    <p style={{ textAlign: 'center', fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '8px' }}>
                        Forgot your password? Ask your administrator to reset it.
                    </p>
                )}

                {canRequestAccess ? (
                    <p style={{ textAlign: 'center', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                        {isRegistering ? 'Already have an account? ' : "Need an account? "}
                        <a href="#" onClick={switchMode} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                            {isRegistering ? 'Sign In' : 'Request Access'}
                        </a>
                    </p>
                ) : (
                    /* `closed`: no form, no toggle, and one line saying where an
                       account comes from instead. Nothing here reveals anything
                       about who already has one. */
                    <p style={{ textAlign: 'center', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                        Ask your administrator for an account.
                    </p>
                )}

                {isRegistering && (
                    <p style={{ textAlign: 'center', fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '8px' }}>
                        Account requests must be approved by an admin before you can sign in.
                    </p>
                )}

                {/* Issue #262. Disclosure, not a gate: there is deliberately no
                    acceptance checkbox and no server-side field, so this must
                    never become a condition of submitting the form.

                    A plain anchor rather than a router <Link>: it opens in a new
                    tab so a half-filled request survives the detour, and this
                    page is rendered outside the router in its own tests. /terms
                    is served by the SPA fallback in web/Dockerfile. */}
                {isRegistering && (
                    <p style={{ textAlign: 'center', fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '8px' }}>
                        <a
                            href="/terms"
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ color: 'var(--accent)', textDecoration: 'none' }}
                        >
                            Terms of use
                        </a>
                        {' '}— what this server does and does not promise, and what you may upload.
                    </p>
                )}
            </div>
        </div>
    )
}

export default LoginPage
