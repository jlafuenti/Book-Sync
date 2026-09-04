/**
 * User management (admin).
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow } from './http';

// ============ User Management (Admin) ============

export async function getUsers(filter) {
    const params = filter ? `?filter=${filter}` : '';
    const resp = await fetchWithAuth(`${API_BASE}/users/${params}`);
    return jsonOrThrow(resp, 'Failed to fetch users');
}

export async function createUser(data) {
    const resp = await fetchWithAuth(`${API_BASE}/users/`, {
        method: 'POST',
        body: JSON.stringify(data),
    });
    return jsonOrThrow(resp, 'Failed to create user');
}

export async function updateUser(id, data) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
    });
    return jsonOrThrow(resp, 'Failed to update user');
}

export async function approveUser(id) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}/approve`, {
        method: 'POST',
    });
    return jsonOrThrow(resp, 'Failed to approve user');
}

export async function resetUserPassword(id, newPassword) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}/reset-password`, {
        method: 'POST',
        body: JSON.stringify({ new_password: newPassword }),
    });
    return jsonOrThrow(resp, 'Failed to reset password');
}

export async function deleteUser(id) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Failed to delete user');
}

export async function getAuditLog(page = 1, limit = 50, action, userId) {
    let params = `?page=${page}&limit=${limit}`;
    if (action) params += `&action=${action}`;
    if (userId) params += `&user_id=${userId}`;
    const resp = await fetchWithAuth(`${API_BASE}/users/audit-log${params}`);
    return jsonOrThrow(resp, 'Failed to fetch audit log');
}
