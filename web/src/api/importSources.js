/**
 * Import sources: Audible, ACSM.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow } from './http';

/* ── Import Sources ──────────────────────────────────────────────────── */

export async function listImportSources() {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources`);
    return jsonOrThrow(resp, 'Failed to list import sources');
}

export async function updateImportSourceConfig(sourceKey, config) {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources/${sourceKey}/config`, {
        method: 'PUT',
        body: JSON.stringify(config),
    });
    return jsonOrThrow(resp, 'Failed to update source config');
}

export async function triggerImportSync(sourceKey) {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources/${sourceKey}/sync`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to trigger sync');
}

export async function getImportJobs(sourceKey) {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources/${sourceKey}/jobs`);
    return jsonOrThrow(resp, 'Failed to fetch jobs');
}

export async function audibleLoginStart() {
    const resp = await fetchWithAuth(`${API_BASE}/import/audible/login/start`, {
        method: 'POST',
        body: JSON.stringify({}),
    });
    return jsonOrThrow(resp, 'Failed to start Audible login');
}

export async function audibleLoginComplete(stateToken, responseUrl) {
    const resp = await fetchWithAuth(`${API_BASE}/import/audible/login/complete`, {
        method: 'POST',
        body: JSON.stringify({ state_token: stateToken, response_url: responseUrl }),
    });
    return jsonOrThrow(resp, 'Audible login failed');
}

export async function audibleDisconnect() {
    const resp = await fetchWithAuth(`${API_BASE}/import/audible/disconnect`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to disconnect Audible');
}

export async function uploadAcsm(file) {
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/upload`, {
        method: 'POST',
        body: fd,
    });
    return jsonOrThrow(resp, 'Upload failed');
}

export async function acsmAuthorizationStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/authorization`);
    return jsonOrThrow(resp, 'Failed to check authorization');
}

export async function acsmAuthorize({ mode, email = '', password = '' }) {
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/authorize`, {
        method: 'POST',
        body: JSON.stringify({ mode, email, password }),
    });
    return jsonOrThrow(resp, 'Authorization failed');
}

export async function acsmDeauthorize() {
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/deauthorize`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to revoke authorization');
}
