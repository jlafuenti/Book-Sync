/**
 * BookSync API Client
 * 
 * Handles all HTTP communication with the FastAPI server,
 * including JWT token management.
 */

const API_BASE = '/api';

let accessToken = localStorage.getItem('booksync_token');
let refreshToken = localStorage.getItem('booksync_refresh');

function setTokens(access, refresh) {
    accessToken = access;
    refreshToken = refresh;
    localStorage.setItem('booksync_token', access);
    localStorage.setItem('booksync_refresh', refresh);
}

function clearTokens() {
    accessToken = null;
    refreshToken = null;
    localStorage.removeItem('booksync_token');
    localStorage.removeItem('booksync_refresh');
}

async function fetchWithAuth(url, options = {}) {
    const headers = { ...options.headers };
    if (accessToken) {
        headers['Authorization'] = `Bearer ${accessToken}`;
    }
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    let response = await fetch(url, { ...options, headers });

    // If 401, try refreshing the token
    if (response.status === 401 && refreshToken) {
        const refreshResp = await fetch(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
        });

        if (refreshResp.ok) {
            const data = await refreshResp.json();
            setTokens(data.access_token, data.refresh_token);
            headers['Authorization'] = `Bearer ${data.access_token}`;
            response = await fetch(url, { ...options, headers });
        } else {
            clearTokens();
            window.location.href = '/login';
        }
    }

    return response;
}

// ============ Auth ============

export async function login(username, password) {
    const resp = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Login failed');
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
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Registration failed');
    return resp.json();
}

export async function getMe() {
    const resp = await fetchWithAuth(`${API_BASE}/auth/me`);
    if (!resp.ok) return null;
    return resp.json();
}

export function logout() {
    clearTokens();
}

export function isLoggedIn() {
    return !!accessToken;
}

// ============ Library ============

export async function scanLibrary() {
    const resp = await fetchWithAuth(`${API_BASE}/library/scan`, { method: 'POST' });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Scan failed');
    return resp.json();
}

export async function normalizeLibrary() {
    const resp = await fetchWithAuth(`${API_BASE}/library/normalize`, { method: 'POST' });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Normalize failed');
    return resp.json();
}

export async function getEbooks() {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks`);
    if (!resp.ok) throw new Error('Failed to fetch ebooks');
    return resp.json();
}

export async function getAudiobooks() {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks`);
    if (!resp.ok) throw new Error('Failed to fetch audiobooks');
    return resp.json();
}

export async function getPairs() {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs`);
    if (!resp.ok) throw new Error('Failed to fetch book pairs');
    return resp.json();
}

export async function createPair(ebookId, audiobookId) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs`, {
        method: 'POST',
        body: JSON.stringify({ ebook_id: ebookId, audiobook_id: audiobookId }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to create pair');
    return resp.json();
}

export async function deletePair(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}`, {
        method: 'DELETE',
    });
    if (!resp.ok) throw new Error('Failed to delete pair');
}

export async function uploadEbook(file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/upload/ebook`, {
        method: 'POST',
        body: formData,
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Upload failed');
    return resp.json();
}

export async function uploadAudiobook(file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/upload/audiobook`, {
        method: 'POST',
        body: formData,
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Upload failed');
    return resp.json();
}

// ============ Transcription ============

export async function startTranscription(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/start`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to start transcription');
    return resp.json();
}

export async function getTranscriptionStatus(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/status`);
    if (!resp.ok) throw new Error('Failed to get status');
    return resp.json();
}

export async function cancelTranscription(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/cancel`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error('Failed to cancel transcription');
    return resp.json();
}

// ============ Sync ============

export async function getSyncMap(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/files/syncmap/${pairId}`);
    if (!resp.ok) throw new Error('Failed to get sync map');
    return resp.json();
}

// ============ Stats ============

export async function getDiskUsage() {
    const resp = await fetchWithAuth(`${API_BASE}/stats/disk_usage`);
    if (!resp.ok) throw new Error('Failed to fetch disk usage');
    return resp.json();
}

// ============ Settings ============

export async function getSettings() {
    const resp = await fetchWithAuth(`${API_BASE}/settings/`);
    if (!resp.ok) throw new Error('Failed to fetch settings');
    return resp.json();
}

export async function updateSettings(settings) {
    const resp = await fetchWithAuth(`${API_BASE}/settings/`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(settings),
    });
    if (!resp.ok) throw new Error('Failed to update settings');
    return resp.json();
}

export async function updateEbookMetadata(bookId, meta) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${bookId}`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(meta),
    });
    if (!resp.ok) throw new Error('Failed to update ebook metadata');
    return resp.json();
}

export async function updateAudiobookMetadata(bookId, meta) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${bookId}`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(meta),
    });
    if (!resp.ok) throw new Error('Failed to update audiobook metadata');
    return resp.json();
}

