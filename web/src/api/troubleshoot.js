/**
 * Troubleshoot: library issues and repairs.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow } from './http';

// ============ Troubleshoot Library ============

export async function getLibraryIssues() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/issues`);
    return jsonOrThrow(resp, 'Failed to load library issues');
}

export async function startLibraryScan() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/scan`, { method: 'POST' });
    return jsonOrThrow(resp, 'Failed to start scan');
}

export async function getLibraryScanProgress() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/scan/progress`);
    return jsonOrThrow(resp, 'Failed to get scan progress');
}

export async function cancelLibraryScan() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/scan/cancel`, { method: 'POST' });
    return jsonOrThrow(resp, 'Failed to cancel scan');
}

export async function bulkDeleteIssues(items, deleteFile = true) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/bulk-delete?delete_file=${deleteFile}`, {
        method: 'POST',
        body: JSON.stringify({ items }),
    });
    return jsonOrThrow(resp, 'Bulk delete failed');
}

export async function replaceLibraryFile(itemType, itemId, file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/replace/${itemType}/${itemId}`, {
        method: 'POST',
        body: formData,
    });
    return jsonOrThrow(resp, 'Replace failed');
}

export async function requeuePair(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/requeue/${pairId}`, { method: 'POST' });
    return jsonOrThrow(resp, 'Requeue failed');
}

export async function repairChapterEncoding(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/repair-chapter-encoding/${itemId}`, { method: 'POST' });
    return jsonOrThrow(resp, 'Repair failed');
}

export async function bulkRepairChapterEncoding(itemIds) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/bulk-repair-chapter-encoding`, {
        method: 'POST',
        body: JSON.stringify({ item_ids: itemIds }),
    });
    return jsonOrThrow(resp, 'Bulk repair failed');
}

export async function dismissFailedAcsm(filename) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/acsm-dismiss`, {
        method: 'POST',
        body: JSON.stringify({ filename }),
    });
    return jsonOrThrow(resp, 'Dismiss failed');
}

// Multi-file audiobook folders (issue #63): flagged by the library scan, not
// imported. Dismiss hides one until its contents change; remove-tracks deletes
// the AudioBook rows imported one-per-track before detection existed (files
// stay on disk for merging in Audiobookshelf).
export async function dismissMultiFileFolder(folderId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/multi-file/${folderId}/dismiss`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Dismiss failed');
}

export async function removeMultiFileTracks(folderId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/multi-file/${folderId}/remove-tracks`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Removing imported tracks failed');
}

export async function deleteOrphanCovers(filenames) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/delete-orphan-covers`, {
        method: 'POST',
        body: JSON.stringify({ filenames }),
    });
    return jsonOrThrow(resp, 'Delete failed');
}

// Sync-map drift/degraded audit (issue #295, issue #586): a map built against
// the wrong ebook file, or one whose audio content is reordered relative to
// the ebook. `flagged_only` is the page's default — an operator wants the
// pairs that need attention, not a walk of every healthy one.
export async function getSyncMapAudit({ flaggedOnly = true } = {}) {
    const resp = await fetchWithAuth(
        `${API_BASE}/troubleshoot/sync-map-audit?flagged_only=${flaggedOnly}`
    );
    return jsonOrThrow(resp, 'Sync-map audit failed');
}

// Rebuild of sync maps built by an older sentence splitter (issue #774) or
// before sync points kept whole sentences (issue #763).
// `jsonOrThrow` keeps only the message, but the card has to tell a 404 (older
// server, no such endpoint) from a 409 (a run is already active), so these
// three put the HTTP status on `err.status`.
async function rebuildJson(resp, fallback) {
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const err = new Error(typeof body.detail === 'string' && body.detail ? body.detail : fallback);
        err.status = resp.status;
        throw err;
    }
    return resp.json();
}

export async function getSyncMapRebuildStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/sync-map-rebuild`);
    return rebuildJson(resp, 'Failed to load sync-map rebuild status');
}

export async function startSyncMapRebuild({ dryRun, pairIds = null, limit = null }) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/sync-map-rebuild`, {
        method: 'POST',
        body: JSON.stringify({ dry_run: dryRun, pair_ids: pairIds, limit }),
    });
    return rebuildJson(resp, 'Failed to start the sync-map rebuild');
}

// Word-timing coverage (issue #835): how many cached transcripts carry
// word-level timing, and a bulk queue of the ones that do not. Both put the HTTP
// status on `err.status` (the card hides itself on a 404, an older server).
export async function getWordTimingStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/word-timing`);
    return rebuildJson(resp, 'Failed to load word-timing coverage');
}

export async function queueWordTiming() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/word-timing/queue`, { method: 'POST' });
    return rebuildJson(resp, 'Failed to queue transcripts for word timing');
}

export async function cancelSyncMapRebuild() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/sync-map-rebuild/cancel`, { method: 'POST' });
    return rebuildJson(resp, 'Failed to cancel the sync-map rebuild');
}
