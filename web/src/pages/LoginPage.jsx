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
                <h1>📖 BookSync</h1>
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
            </div>
        </div>
    )
}

export default LoginPage
