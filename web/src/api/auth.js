/**
 * Authentication and the signed-in account.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import {
    API_BASE, fetchWithAuth, jsonOrThrow, errorMessage,
    setTokens, clearTokens, getRefreshToken, getDeviceId,
} from './http';

export async function login(username, password) {
    const resp = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The session this opens is named after the browser, so signing out
        // here signs out only here (issue #250).
        body: JSON.stringify({ username, password, device_id: getDeviceId() }),
    });
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Login failed'));
    const data = await resp.json();
    setTokens(data.access_token, data.refresh_token);
    return data;
}

export async function register(username, email, password, inviteCode) {
    const resp = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The code only travels when the server is in `invite` mode and the user
        // typed one (issue #210). Omitted rather than sent empty, so an `open`
        // server sees exactly the body it always saw.
        body: JSON.stringify(
            inviteCode ? { username, email, password, invite_code: inviteCode }
                : { username, email, password },
        ),
    });
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Registration failed'));
    return resp.json();
}

/**
 * How this server treats a stranger: 'open' | 'invite' | 'closed' (issue #210).
 *
 * Unauthenticated — the login page asks before anyone has a token, to decide
 * whether to offer a request form, a request form with an invite-code field, or
 * a line telling the visitor to ask their administrator.
 *
 * Falls back to 'open' on anything unexpected: an older server with no such
 * endpoint, a 502 from the proxy, a body this build does not recognise. Failing
 * closed here would hide the request form from a whole deployment over a
 * transient error, which is a worse outcome than briefly offering a form whose
 * submit the server then refuses.
 */
export async function getRegistrationMode() {
    try {
        const resp = await fetch(`${API_BASE}/auth/registration`);
        if (!resp.ok) return 'open';
        const data = await resp.json();
        return ['open', 'invite', 'closed'].includes(data?.mode) ? data.mode : 'open';
    } catch {
        return 'open';
    }
}

/**
 * Invites (issue #210) — admin only. `createInvite` is the one and only time a
 * code is returned; nothing stores it and it cannot be read back.
 */
export async function createInvite() {
    const resp = await fetchWithAuth(`${API_BASE}/auth/invites`, { method: 'POST' });
    return jsonOrThrow(resp, 'Failed to create invite');
}

export async function getInvites() {
    const resp = await fetchWithAuth(`${API_BASE}/auth/invites`);
    return jsonOrThrow(resp, 'Failed to load invites');
}

export async function revokeInvite(id) {
    const resp = await fetchWithAuth(`${API_BASE}/auth/invites/${id}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Failed to revoke invite'));
}

export async function getMe() {
    const resp = await fetchWithAuth(`${API_BASE}/auth/me`);
    if (!resp.ok) return null;
    return resp.json();
}

export async function logout() {
    // Best-effort: invalidate server-side tokens, but always clear local
    // tokens even if the request fails (e.g. offline, token already expired).
    //
    // The refresh token goes in the body to name the session to end (issue
    // #250) — this browser's, not the phone's, which is usually mid-book with
    // positions it has not pushed yet. The server falls back to the session
    // named by the access token, so an old client is not signed out everywhere
    // by accident either; sending it explicitly just means the call still names
    // the right session when the access token is the thing that has expired.
    const presented = getRefreshToken();
    try {
        await fetchWithAuth(`${API_BASE}/auth/logout`, {
            method: 'POST',
            body: JSON.stringify({ refresh_token: presented }),
        });
    } catch {
        // ignore — local logout must still succeed
    }
    clearTokens();
}

export async function updateMe(data) {
    const resp = await fetchWithAuth(`${API_BASE}/auth/me`, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
    return jsonOrThrow(resp, 'Failed to update profile');
}

export async function changePassword(oldPassword, newPassword) {
    const resp = await fetchWithAuth(`${API_BASE}/auth/change-password`, {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    return jsonOrThrow(resp, 'Failed to change password');
}

export async function logoutAll() {
    // The account-wide sign-out (issue #250). `logout` deliberately ends only
    // this browser's session; this is the one to reach for when a device has
    // been lost, and it is the *only* thing that still bumps `token_version`
    // from a normal user action.
    //
    // No body: the endpoint takes none, because there is no session to name --
    // every one of them is being revoked. Best-effort like `logout`, for the
    // same reason: a failed request must not leave the browser signed in with
    // tokens the user has just asked to destroy.
    try {
        await fetchWithAuth(`${API_BASE}/auth/logout-all`, { method: 'POST' });
    } catch {
        // ignore -- the local half must still happen
    }
    clearTokens();
}

export async function deleteAccount(password) {
    const resp = await fetchWithAuth(`${API_BASE}/auth/me`, {
        method: 'DELETE',
        body: JSON.stringify({ password }),
    });
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Failed to delete account'));
    clearTokens();
}

