import React, { useState } from 'react'
import { logoutAll } from '../api'

export const SIGN_OUT_EVERYWHERE_CONFIRM =
    'Sign out on every device?\n\n' +
    'This ends every session on this account — your phone, your tablet and any ' +
    'other browser — not just this one. Use it if a device has been lost.\n\n' +
    'A device that is signed out cannot push reading positions it has not synced ' +
    'yet; they stay on that device until it signs in again.'

/**
 * The client half of `POST /api/auth/logout-all` (issue #250).
 *
 * Sign-out became per-device so that closing the browser would stop signing the
 * phone out mid-book. That left the account-wide revoke reachable only by curl,
 * with docs/operations.md telling operators to call it by hand — which is no
 * answer at all for the case it exists for: a lost device, where the person who
 * needs it is the user and not the operator.
 *
 * Deliberately not the same control as Logout. It bumps `token_version`, so it
 * is the one normal user action that kills every token the account holds, and it
 * cannot be undone from here — hence the confirm, and hence the wording, which
 * says what happens to unsynced positions (docs/position-sync-contract.md).
 *
 * `logoutAll()` clears the local tokens itself, success or failure; this only
 * has to tell the app the session is over.
 *
 * When the account page from issue #146 lands this belongs next to the
 * delete-account control there, rather than in the two nav surfaces it sits in
 * today.
 */
export default function SignOutEverywhere({ onSignedOut, className }) {
    const [busy, setBusy] = useState(false)

    const handleClick = async () => {
        if (busy) return
        if (!window.confirm(SIGN_OUT_EVERYWHERE_CONFIRM)) return
        setBusy(true)
        try {
            await logoutAll()
        } catch {
            // `logoutAll` already swallows its own failures, but a click handler
            // must not be the thing that turns a future change of heart there
            // into an unhandled rejection. The local half happens either way.
        } finally {
            onSignedOut()
        }
    }

    return (
        <button
            type="button"
            className={className}
            data-testid="sign-out-everywhere"
            onClick={handleClick}
            disabled={busy}
            title="Sign out on every device"
        >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
                <line x1="12" y1="3" x2="12" y2="21" />
            </svg>
            <span>{busy ? 'Signing out…' : 'Sign out everywhere'}</span>
        </button>
    )
}
