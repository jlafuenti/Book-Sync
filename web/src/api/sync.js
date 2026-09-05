/**
 * Sync maps, progress and the canonical position.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, jsonOrThrow, getAccessToken } from './http';

// ============ Sync ============

export async function getSyncMap(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/files/syncmap/${pairId}`);
    return jsonOrThrow(resp, 'Failed to get sync map');
}

// ============ Progress ============

export async function getAllProgress() {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress`);
    return jsonOrThrow(resp, 'Failed to fetch all progress');
}

export async function getProgress(mediaType, mediaId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress/${mediaType}/${mediaId}`);
    // 204 = this user has no recorded position for this media. The endpoint used
    // to INSERT a blank row on a miss and answer 200 with it, which is how one
    // book could end up with two rows and 500 forever after (issue #64). There
    // is no body to parse now — null is the position, not an error.
    if (resp.status === 204) return null;
    return jsonOrThrow(resp, 'Failed to fetch progress');
}

// There is no `updateProgress` — `user_progress` is a read-only projection of
// the canonical record (issue #102). Write with `updatePosition` below.

export async function resetPairProgress(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress/pair/${pairId}`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Failed to reset pair progress');
}

// ============ Canonical position ============
//
// One record per book, written atomically. Replaced the pair of
// updateProgress + updateBookmark calls, which were adjudicated separately —
// either could be rejected while the other applied, leaving the two rows
// describing different positions with nothing to reconcile them.

/**
 * The canonical position, or null when the user has none.
 *
 * A 204 means "never opened". It is deliberately distinct from a position at
 * chapter 0: the reader must be able to tell "no position" from "at the start".
 */
export async function getPosition(scope, id) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/position/${scope}/${id}`);
    if (resp.status === 204) return null;
    return jsonOrThrow(resp, 'Failed to fetch position');
}

export async function updatePosition(scope, id, position) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/position/${scope}/${id}`, {
        method: 'PUT',
        body: JSON.stringify(position),
    });
    // Same convention as updateProgress/updateBookmark: a 409 is an expected
    // "rejected — server state is newer" result carrying the authoritative
    // record, not an error. Nothing was written.
    if (resp.status === 409) {
        const body = await resp.json();
        return Object.assign(body, { rejected: true });
    }
    return jsonOrThrow(resp, 'Failed to update position');
}

/**
 * Clear the user's position for a scope — the canonical reset.
 *
 * Standalone (unpaired) media had no reset endpoint, so this used to be faked
 * by PUTting zeros through the legacy progress adapter. That left the
 * canonical record in place for the next write to resurrect, which is how
 * "Reset Progress" silently un-reset itself.
 */
export async function resetPosition(scope, id) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/position/${scope}/${id}`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Failed to reset position');
}

// ============ Bookmarks ============
//
// Only the history log remains — the bookmark GET/PUT went with the legacy
// adapters (issue #102). Read and write positions with getPosition/updatePosition.

export async function getBookmarkLog(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/bookmark/${pairId}/log`);
    return jsonOrThrow(resp, 'Failed to fetch bookmark log');
}

/**
 * Fire-and-forget position update that survives page unload.
 *
 * Used by AudioPlayerContext's `beforeunload` / `pagehide` handler to log a
 * final "stop" history entry when the user closes the tab. A regular fetch
 * would be aborted during unload; `keepalive: true` tells the browser to let
 * the request complete in the background (up to ~64 KB body).
 *
 * Not awaited — errors are swallowed because there is no UI context left to
 * surface them in. Silent failure is acceptable: the last 5-second heartbeat
 * save kept the position fresh, so at worst we lose the history-log entry,
 * not the resume position.
 *
 * It must be able to OMIT `source` when the player wasn't actively playing at
 * unload (product rule: a save only claims the format when playing or
 * triggered by an explicit user command — see AudioPlayerContext's
 * `onUnload`). `PositionUpdate` treats an omitted `source` as "keep whatever
 * is stored" (server `schemas.PositionUpdate`).
 */
export function sendPositionKeepalive(scope, id, data) {
    // Read once: this deliberately bypasses `fetchWithAuth`, because a 401
    // here has no retry to come back to — the document is going away.
    const token = getAccessToken();
    if (!token) return;
    try {
        fetch(`${API_BASE}/sync/position/${scope}/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify(data),
            keepalive: true,
        });
    } catch {}
}

/**
 * Resolve an audio position to its EPUB coordinates through the pair's sync
 * map (issue #159) — the audio rung of the restore ladder, executable from
 * the web. Returns `{ epub_chapter, epub_sentence_index, preview,
 * sync_map_version }`, or null when the pair has no sync map (or the call
 * fails): the caller treats null as "this rung cannot land", never an error.
 */
export async function audioToEpub(pairId, audioPositionMs) {
    const resp = await fetchWithAuth(
        `${API_BASE}/sync/audio-to-epub/${pairId}?audio_ms=${Math.max(0, Math.floor(audioPositionMs))}`
    );
    if (!resp.ok) return null;
    return resp.json();
}

export async function matchTextToAudio(pairId, epubText, chapterHint = 0) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/match-text/${pairId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ epub_text: epubText, chapter_hint: chapterHint }),
    });
    if (!resp.ok) return null;
    return resp.json();
}
