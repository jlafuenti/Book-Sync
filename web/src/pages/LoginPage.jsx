import React, { useState } from 'react'
import { login, register, getMe } from '../api'

function LoginPage({ onLogin }) {
    const [isRegistering, setIsRegistering] = useState(false)
    const [username, setUsername] = useState('')
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)

        try {
            if (isRegistering) {
                await register(username, email, password)
                // Auto-login after registration
                await login(username, password)
            } else {
                await login(username, password)
            }
            const user = await getMe()
            onLogin(user)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="login-page">
            <div className="login-card">
                <h1>📖 BookSync</h1>
                <p className="subtitle">
                    {isRegistering ? 'Create your account' : 'Sign in to your account'}
                </p>

                {error && <div className="alert alert-error">⚠️ {error}</div>}

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label>Username</label>
                        <input
                            type="text"
                            className="form-input"
                            placeholder="Enter username"
                            value={username}
                            onChange={e => setUsername(e.target.value)}
                            required
                        />
                    </div>

                    {isRegistering && (
                        <div className="form-group">
                            <label>Email</label>
                            <input
                                type="email"
                                className="form-input"
                                placeholder="Enter email"
                                value={email}
                                onChange={e => setEmail(e.target.value)}
                                required
                            />
                        </div>
                    )}

                    <div className="form-group">
                        <label>Password</label>
                        <input
                            type="password"
                            className="form-input"
                            placeholder="Enter password"
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
                        {loading ? <div className="spinner"></div> : (isRegistering ? 'Create Account' : 'Sign In')}
                    </button>
                </form>

                <p style={{ textAlign: 'center', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                    {isRegistering ? 'Already have an account? ' : "Don't have an account? "}
                    <a
                        href="#"
                        onClick={(e) => { e.preventDefault(); setIsRegistering(!isRegistering); setError('') }}
                        style={{ color: 'var(--accent)', textDecoration: 'none' }}
                    >
                        {isRegistering ? 'Sign In' : 'Register'}
                    </a>
                </p>
            </div>
        </div>
    )
}

export default LoginPage
