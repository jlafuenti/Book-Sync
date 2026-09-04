/**
 * Disk usage and backups.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, ensureOk, jsonOrThrow } from './http';

// ============ Stats ============

export async function getDiskUsage() {
    const resp = await fetchWithAuth(`${API_BASE}/stats/disk_usage`);
    return jsonOrThrow(resp, 'Failed to fetch disk usage');
}

// ============ Backups (issue #60) ============

export async function getBackupStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backup`);
    return jsonOrThrow(resp, 'Failed to fetch backup status');
}

export async function listBackups() {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups`);
    return jsonOrThrow(resp, 'Failed to list backups');
}

export async function restoreBackup(backupId) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backup_id: backupId, confirm: true }),
    });
    return jsonOrThrow(resp, 'Failed to restore backup');
}

export async function createBackup(label) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: label || null }),
    });
    return jsonOrThrow(resp, 'Failed to create backup');
}

export async function deleteBackup(backupId) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups/${encodeURIComponent(backupId)}`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Failed to delete backup');
}

// Fetch the .dump with the auth header, then trigger a browser download of the
// blob (an <a href> can't carry the Authorization header).
export async function downloadBackup(backupId) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups/${encodeURIComponent(backupId)}/download`);
    await ensureOk(resp, 'Failed to download backup');
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `booksync-db-${backupId}.dump`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}
