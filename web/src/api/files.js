/**
 * Media tokens and file access.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, ensureOk, getMediaToken } from './http';

/**
 * Mint media tokens for multiple resources in one round-trip (e.g. all covers
 * visible on a library grid page) so individual coverSrc() calls resolve
 * from cache instead of firing N separate requests.
 *
 * Since #269 this is a thin wrapper over the same queue getMediaToken uses, so
 * a prefetch and the covers that render alongside it collapse into one call
 * rather than two. Callers that only want the cache warmed can ignore the
 * result: a resource that could not be minted is skipped, not thrown.
 */
export async function prefetchMediaTokens(resources) {
    if (!resources.length) return;
    await Promise.all(resources.map(
        (r) => getMediaToken(r.resourceType, r.resourceId).catch(() => null)
    ));
}

/**
 * Append a short-lived, resource-scoped ?token= to a cover_path URL so the
 * browser <img> tag can authenticate against the /api/files/covers/ endpoint
 * without exposing the long-lived login token in the URL.
 * Returns null/undefined as-is so callers can still check falsiness.
 */
export async function coverSrc(path) {
    if (!path) return path;
    const qIndex = path.indexOf('?');
    const rawPath = qIndex === -1 ? path : path.slice(0, qIndex);
    const query = qIndex === -1 ? '' : path.slice(qIndex + 1);
    const segments = rawPath.split('/');
    // Encode only the filename segment. A '#' in a cover name would otherwise be
    // read as a fragment delimiter, so the request path gets truncated and the
    // ?token= never reaches the server -- a 401 (issue #126). encodeURIComponent
    // also covers ' ', '?', '&' and '%'. The token stays scoped to the RAW
    // filename: the server percent-decodes the path param before comparing.
    const filename = segments.pop();
    const token = await getMediaToken('cover', filename);
    const encodedPath = [...segments, encodeURIComponent(filename)].join('/');
    return `${encodedPath}?${query ? `${query}&` : ''}token=${token}`;
}

// ============ File Access ============

export async function fetchEbookBlob(ebookId) {
    const resp = await fetchWithAuth(`${API_BASE}/files/ebook/${ebookId}`);
    await ensureOk(resp, 'Failed to fetch ebook file');
    return resp.arrayBuffer();
}

export async function getAudiobookStreamUrl(audiobookId) {
    const token = await getMediaToken('audiobook', String(audiobookId));
    return `${API_BASE}/files/audiobook/${audiobookId}?token=${token}`;
}
