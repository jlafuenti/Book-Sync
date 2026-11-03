/**
 * Series removed from Home's Next up (issue #922).
 *
 * `src/api.js` re-exports this, so every `from '../api'` import and every
 * `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow, ensureOk } from './http';

export async function getNextUpDismissals() {
    const resp = await fetchWithAuth(`${API_BASE}/next-up/dismissals`);
    return jsonOrThrow(resp, 'Failed to fetch Next up removals');
}

export async function dismissNextUpSeries(series) {
    const resp = await fetchWithAuth(`${API_BASE}/next-up/dismissals`, {
        method: 'POST',
        body: JSON.stringify({ series }),
    });
    return jsonOrThrow(resp, 'Failed to remove series from Next up');
}

export async function undismissNextUpSeries(series) {
    const resp = await fetchWithAuth(
        `${API_BASE}/next-up/dismissals?series=${encodeURIComponent(series)}`,
        { method: 'DELETE' },
    );
    await ensureOk(resp, 'Failed to restore series to Next up');
}
