import React from 'react'
import ChangePasswordForm from '../components/ChangePasswordForm'

/**
 * The forced-reset screen: shown instead of the app while the account has
 * `must_reset_password` set (an admin reset, or the break-glass script).
 *
 * A success revokes every session, this one included, so there is no signed-in
 * user to hand back. It used to call `getMe()` with the dead tokens and pass on
 * the null it got, which dropped the user at the login screen with no word of
 * why (issue #962). It now just reports the change; App says so on the login
 * screen.
 */
function ChangePasswordPage({ onPasswordChanged }) {
    return (
        <div className="login-page">
            <div className="login-card">
                <h1>📖 Tandem</h1>
                <p className="subtitle">Password Reset Required</p>
                <p style={{ textAlign: 'center', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '16px' }}>
                    You must set a new password before continuing.
                </p>

                <ChangePasswordForm
                    idPrefix="reset"
                    submitLabel="Set New Password"
                    busyLabel="Setting…"
                    onChanged={() => onPasswordChanged()}
                />
            </div>
        </div>
    )
}

export default ChangePasswordPage
