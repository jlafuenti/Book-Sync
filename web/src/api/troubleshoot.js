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
