/**
 * Transcription and its queue.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow } from './http';

// ============ Transcription ============

export async function startTranscription(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/start`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to start transcription');
}

export async function getTranscriptionStatus(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/status`);
    return jsonOrThrow(resp, 'Failed to get status');
}

export async function cancelTranscription(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/cancel`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to cancel transcription');
}

export async function updateTranscriptionText(pairId, points) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/${pairId}/text`, {
        method: 'PUT',
        body: JSON.stringify({ points }),
    });
    return jsonOrThrow(resp, 'Failed to update transcription text');
}

// ============ Transcription Queue ============

export async function getTranscriptionQueue() {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue`);
    return jsonOrThrow(resp, 'Failed to fetch transcription queue');
}

export async function addToQueue(pairIds) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/batch`, {
        method: 'POST',
        body: JSON.stringify({ pair_ids: pairIds }),
    });
    return jsonOrThrow(resp, 'Failed to add to queue');
}

export async function removeFromQueue(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Failed to remove from queue');
}

export async function updateQueuePriority(itemId, priority) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}/priority`, {
        method: 'PUT',
        body: JSON.stringify({ priority }),
    });
    return jsonOrThrow(resp, 'Failed to update priority');
}

export async function getQueueHistory(limit = 50, offset = 0) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/history?limit=${limit}&offset=${offset}`);
    return jsonOrThrow(resp, 'Failed to fetch queue history');
}

// Dispatch a queued item immediately, ignoring the off-hours window (#106).
export async function runQueueItemNow(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}/run-now`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to start item');
}

// Retry a failed or cancelled history item (#247). The server queues a *new*
// pending row for the same pair — the failed row stays in History.
export async function requeueQueueItem(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}/requeue`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to retry item');
}

// Current off-hours window state, for the queue page banner.
export async function getOffHoursStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/offhours`);
    return jsonOrThrow(resp, 'Failed to fetch off-hours status');
}
