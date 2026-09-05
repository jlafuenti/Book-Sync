import React, { useState } from 'react'
import { login, register, getMe } from '../api'

function LoginPage({ onLogin }) {
    const [isRegistering, setIsRegistering] = useState(false)
    const [username, setUsername] = useState('')
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setSuccess('')
        setLoading(true)

        try {
            if (isRegistering) {
                const data = await register(username, email, password)
                setSuccess(data.message || 'Access request submitted. An admin must approve your account before you can sign in.')
                setUsername('')
                setEmail('')
                setPassword('')
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

                <p style={{ textAlign: 'center', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                    {isRegistering ? 'Already have an account? ' : "Need an account? "}
                    <a href="#" onClick={switchMode} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                        {isRegistering ? 'Sign In' : 'Request Access'}
                    </a>
                </p>

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
