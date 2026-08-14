import React, { useState } from 'react'
import { changePassword, getMe } from '../api'

function ChangePasswordPage({ onPasswordChanged }) {
    const [oldPassword, setOldPassword] = useState('')
    const [newPassword, setNewPassword] = useState('')
    const [confirmPassword, setConfirmPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')

        if (newPassword !== confirmPassword) {
            setError('New passwords do not match')
            return
        }
        if (newPassword.length < 6) {
            setError('Password must be at least 6 characters')
            return
        }

        setLoading(true)
        try {
            await changePassword(oldPassword, newPassword)
            const updatedUser = await getMe()
            onPasswordChanged(updatedUser)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="login-page">
            <div className="login-card">
                <h1>📖 Tandem</h1>
                <p className="subtitle">Password Reset Required</p>
                <p style={{ textAlign: 'center', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '16px' }}>
                    You must set a new password before continuing.
                </p>

                {error && <div className="alert alert-error">⚠️ {error}</div>}

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label htmlFor="old-password">Current Password</label>
                        <input
                            id="old-password"
                            type="password"
                            className="form-input"
                            placeholder="Enter current password"
                            autoComplete="current-password"
                            value={oldPassword}
                            onChange={e => setOldPassword(e.target.value)}
                            required
                        />
                    </div>
                    <div className="form-group">
                        <label htmlFor="new-password">New Password</label>
                        <input
                            id="new-password"
                            type="password"
                            className="form-input"
                            placeholder="Enter new password"
                            autoComplete="new-password"
                            value={newPassword}
                            onChange={e => setNewPassword(e.target.value)}
                            required
                        />
                    </div>
                    <div className="form-group">
                        <label htmlFor="confirm-password">Confirm New Password</label>
                        <input
                            id="confirm-password"
                            type="password"
                            className="form-input"
                            placeholder="Confirm new password"
                            autoComplete="new-password"
                            value={confirmPassword}
                            onChange={e => setConfirmPassword(e.target.value)}
                            required
                        />
                    </div>
                    <button
                        type="submit"
                        className="btn btn-primary"
                        style={{ width: '100%', justifyContent: 'center' }}
                        disabled={loading}
                    >
                        {loading ? <div className="spinner"></div> : 'Set New Password'}
                    </button>
                </form>
            </div>
        </div>
    )
}

export default ChangePasswordPage
