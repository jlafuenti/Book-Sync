import React from 'react'
import { Link } from 'react-router-dom'

/**
 * The public account-deletion page (issue #146).
 *
 * Google Play requires an app that can create accounts to publish a web link
 * "where users can request app account deletion", reachable *without* the app —
 * the audience is someone who has already uninstalled it. So this page is
 * mounted before the auth gate in `App.jsx`, calls no API, and reads nothing
 * from `localStorage`. `App.accountDeletion.test.jsx` pins that it survives the
 * gate, and this page's own test pins that it never imports the API.
 *
 * It also has to be honest about a self-hosted product: there is no "Tandem
 * account" in a central sense, only an account on whichever server the user was
 * given. Someone who cannot sign in has to reach that server's operator, and
 * saying so is more useful than a contact form we could not route.
 */
export default function AccountDeletionPage() {
    return (
        <div className="login-page">
            <div className="login-card" style={{ maxWidth: 640, textAlign: 'left' }}>
                <h1 style={{ textAlign: 'center' }}>📖 Tandem</h1>
                <h2 style={{ fontSize: '1.1rem', marginTop: 8 }}>
                    Delete your Tandem account
                </h2>

                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                    Tandem is self-hosted: your account lives on the Tandem server you
                    were given access to, not on a service run by the app's authors.
                    Deleting it removes it from that server.
                </p>

                <h3 style={{ fontSize: '0.95rem', marginBottom: 4 }}>
                    If you can still sign in
                </h3>
                <ol data-testid="deletion-steps" style={{ paddingLeft: 20, fontSize: '0.9rem' }}>
                    <li>Sign in to Tandem — in the Android app, or in this web app.</li>
                    <li>Open <strong>Account</strong>.</li>
                    <li>
                        Choose <strong>Delete account</strong>, enter your current password
                        and type <code>DELETE</code> to confirm.
                    </li>
                </ol>
                <p style={{ fontSize: '0.9rem' }}>
                    The account is removed immediately. You are signed out on every device.
                </p>

                <h3 style={{ fontSize: '0.95rem', marginBottom: 4 }}>
                    If you can no longer sign in
                </h3>
                <p data-testid="deletion-operator-fallback" style={{ fontSize: '0.9rem' }}>
                    Contact whoever runs your Tandem server — the operator who gave you
                    the address and your account — and ask them to delete it. They can do
                    it from the admin console without your password. If you do not know
                    who that is, the server address the app was pointed at is the place to
                    start.
                </p>

                <h3 style={{ fontSize: '0.95rem', marginBottom: 4 }}>
                    What is deleted
                </h3>
                <p data-testid="deletion-what-goes" style={{ fontSize: '0.9rem' }}>
                    Your account, your bookmarks and reading positions, your per-device
                    position history, and your progress records — all immediately and
                    permanently. Two things do not go: the ebooks and audiobooks
                    themselves, which belong to the server's owner and were never yours to
                    delete, and the server's security audit log, which keeps a record that
                    an account was deleted with your user id removed from it.
                </p>

                <p style={{ marginTop: 20, textAlign: 'center' }}>
                    <Link to="/">Sign in to Tandem</Link>
                </p>
            </div>
        </div>
    )
}
