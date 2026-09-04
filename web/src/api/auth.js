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

export async function register(username, email, password) {
    const resp = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, email, password }),
    });
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Registration failed'));
    return resp.json();
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
