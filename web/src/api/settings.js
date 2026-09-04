/**
 * Server settings and connection tests.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow } from './http';

// ============ Settings ============

export async function getSettings() {
    const resp = await fetchWithAuth(`${API_BASE}/settings/`);
    return jsonOrThrow(resp, 'Failed to fetch settings');
}

export async function updateSettings(settings) {
    const resp = await fetchWithAuth(`${API_BASE}/settings/`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(settings),
    });
    // Surface the server's `detail` — settings validation (e.g. an invalid
    // off-hours window) explains exactly what's wrong, and a generic message
    // would throw that away.
    return jsonOrThrow(resp, 'Failed to update settings');
}

export async function testRemoteConnection(url, key = '') {
    if (!url) throw new Error("URL is required");
    // We ping via the backend to avoid CORS and VPN local-routing issues
    // where the user's browser cannot reach the Jetson directly.
    // POST, not GET: the Jetson key is long-lived and not resource-scoped, and a
    // query string lands in the access log. One was found there (issue #284).
    const resp = await fetchWithAuth(`${API_BASE}/settings/test-remote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, key }),
    });

    return jsonOrThrow(resp, `HTTP ${resp.status}`);
}

export async function generateTranscriptionRemoteKey() {
    const resp = await fetchWithAuth(`${API_BASE}/settings/transcription-remote-key/generate`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to generate key');
}

export async function testAbsConnection(url, token = '') {
    if (!url) throw new Error("URL is required");
    const resp = await fetchWithAuth(`${API_BASE}/settings/test-abs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, token }),
    });
    return jsonOrThrow(resp, `HTTP ${resp.status}`);
}

export async function testHardcoverConnection(token = '') {
    const resp = await fetchWithAuth(`${API_BASE}/settings/test-hardcover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
    });
    return jsonOrThrow(resp, `HTTP ${resp.status}`);
}
