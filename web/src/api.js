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

export async function rescanAllLibrary() {
    const resp = await fetchWithAuth(`${API_BASE}/library/rescan-all`, { method: 'POST' });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Force rescan failed');
    return resp.json();
}

export async function getEbooks() {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks`);
    if (!resp.ok) throw new Error('Failed to fetch ebooks');
    return resp.json();
}

export async function getEbook(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}`);
    if (!resp.ok) throw new Error('Failed to fetch ebook');
    return resp.json();
}

export async function getAudiobooks() {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks`);
    if (!resp.ok) throw new Error('Failed to fetch audiobooks');
    return resp.json();
}

export async function getAudiobook(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}`);
    if (!resp.ok) throw new Error('Failed to fetch audiobook');
    return resp.json();
}

export async function rescanBook(type, id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/${type}s/${id}/rescan`, {
        method: 'POST'
    });
    if (!resp.ok) throw new Error(`Failed to rescan ${type}`);
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

export async function uploadEbookCover(id, file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}/cover`, {
        method: 'POST',
        body: formData,
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to upload cover');
    return resp.json();
}

export async function uploadAudiobookCover(id, file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/cover`, {
        method: 'POST',
        body: formData,
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to upload cover');
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

export async function updateTranscriptionText(pairId, points) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/text`, {
        method: 'PUT',
        body: JSON.stringify({ points }),
    });
    if (!resp.ok) throw new Error('Failed to update transcription text');
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

// ============ Progress ============

export async function getProgress(mediaType, mediaId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress/${mediaType}/${mediaId}`);
    if (!resp.ok) throw new Error('Failed to fetch progress');
    return resp.json();
}

export async function updateProgress(mediaType, mediaId, progressData) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress/${mediaType}/${mediaId}`, {
        method: 'PUT',
        body: JSON.stringify(progressData),
    });
    if (!resp.ok) throw new Error('Failed to update progress');
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

export async function testRemoteConnection(url) {
    if (!url) throw new Error("URL is required");
    // We append /v1/health to test the Jetson fastAPI server
    const fullUrl = url.endsWith('/') ? `${url}v1/health` : `${url}/v1/health`;
    
    // We use a regular fetch here since we're hitting a 3rd party IP directly
    const resp = await fetch(fullUrl, {
        method: 'GET',
        // Give it a short timeout in case the IP is dead
        signal: AbortSignal.timeout(3000)
    });
    
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
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

// ============ Chapters ============

export async function getEbookChapters(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}/chapters`);
    if (!resp.ok) throw new Error('Failed to get ebook chapters');
    return resp.json();
}

export async function getAudiobookChapters(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/chapters`);
    if (!resp.ok) throw new Error('Failed to get audiobook chapters');
    return resp.json();
}

export async function updateAudiobookChapters(id, chapters) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/chapters`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(chapters)
    });
    if (!resp.ok) throw new Error('Failed to update audiobook chapters');
    return resp.json();
}

// ============ Match ============

export async function searchMetadata(provider, query, author) {
    const resp = await fetchWithAuth(`${API_BASE}/library/match/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, query, author })
    });
    if (!resp.ok) {
        let errMessage = 'Failed to search metadata';
        try {
            const errData = await resp.json();
            if (errData && errData.detail) errMessage = errData.detail;
        } catch (e) {
            // Ignore JSON parse errors
        }
        throw new Error(errMessage);
    }
    return resp.json();
}

export async function applyRemoteCover(bookType, bookId, coverUrl) {
    const resp = await fetchWithAuth(`${API_BASE}/library/match/apply-cover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ book_type: bookType, book_id: bookId, cover_url: coverUrl })
    });
    if (!resp.ok) throw new Error('Failed to apply remote cover');
    return resp.json();
}

// ============ Cleanup ============

export async function getMetadataDiscrepancies() {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs-discrepancies`);
    if (!resp.ok) throw new Error('Failed to get metadata discrepancies');
    return resp.json();
}

export async function resolveMetadataDiscrepancies(pairId, resolutions) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}/resolve-discrepancies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(resolutions)
    });
    if (!resp.ok) throw new Error('Failed to resolve metadata discrepancies');
    return resp.json();
}
