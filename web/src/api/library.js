/**
 * Library: books, pairs, uploads, metadata, cleanup.
 *
 * Split out of the flat `api.js` (issue #275); `src/api.js` re-exports it, so
 * every `from '../api'` import and every `vi.mock('../api')` keeps working.
 */

import { API_BASE, fetchWithAuth, ensureOk, jsonOrThrow } from './http';

// ============ Library ============

export async function scanLibrary() {
    const resp = await fetchWithAuth(`${API_BASE}/library/scan`, { method: 'POST' });
    return jsonOrThrow(resp, 'Scan failed');
}

export async function normalizeLibrary() {
    const resp = await fetchWithAuth(`${API_BASE}/library/normalize`, { method: 'POST' });
    return jsonOrThrow(resp, 'Normalize failed');
}

export async function rescanAllLibrary() {
    const resp = await fetchWithAuth(`${API_BASE}/library/rescan-all`, { method: 'POST' });
    return jsonOrThrow(resp, 'Force rescan failed');
}

// ---- Paginated list endpoints (issue #48) ----
//
// The server returns `{items, total, page, limit}` (limit ≤ 500, default 100)
// and accepts `q` for a server-side title/author/series search. The `*Page`
// functions expose one page for a paged UI; the legacy whole-list functions
// (`getEbooks()`, `getPairs()`, ...) keep their array return shape by walking
// every page, for the pages that still assemble lists locally.
//
// **Live checklist of who still walks the whole library** (issue #277) — a page
// leaving this list should leave the comment too:
//
//   HomePage         getEbooks + getAudiobooks + getPairs — builds Continue /
//                    Finished / Up-next shelves across all three lists at once.
//                    Wants a `/library/summary`-style aggregate; filed apart.
//   SeriesPage       getEbooks + getAudiobooks + getPairs — groups the whole
//                    library by series client-side. Server-side `series` facet
//                    exists; moving it onto getLibraryItemsPage is filed apart.
//   SystemPage       getEbooks + getAudiobooks + getPairs — only to *count*
//                    them; wants the same aggregate as Home.
//   PairsPage        getPairs + getUnpairedMedia — renders every pair.
//   TranscriptionPage getPairs — partitions every pair by transcription state.
//   LibraryPage      fetchAllPages over getLibraryItemsPage, and only for
//                    "select all in tab" / new-pair sweeps — bounded by an
//                    explicit user action, not a page load.
//
// Migrated: LibraryPage's main list is server-driven (issue #120,
// `getLibraryItemsPage`), UnpairedPage uses `getUnpairedMedia()`, and
// TranscriptionEditorPage uses `getPair()` (issue #277).

const FETCH_ALL_PAGE_SIZE = 500;

function pageQuery({ page = 1, limit = 100, q } = {}) {
    const params = new URLSearchParams({ page: String(page), limit: String(limit) });
    if (q) params.set('q', q);
    return `?${params.toString()}`;
}

async function fetchPage(path, opts, what) {
    const resp = await fetchWithAuth(`${API_BASE}${path}${pageQuery(opts)}`);
    return jsonOrThrow(resp, `Failed to fetch ${what}`);
}

// Walk `fetchOnePage(page)` until a page comes back short (or `total` is
// reached), returning every item. Exported for tests and for callers that
// page an endpoint not wrapped here.
export async function fetchAllPages(fetchOnePage) {
    const all = [];
    for (let page = 1; ; page++) {
        const body = await fetchOnePage(page);
        const items = body?.items || [];
        all.push(...items);
        const limit = body?.limit || items.length || 1;
        if (items.length < limit) break;
        if (typeof body?.total === 'number' && all.length >= body.total) break;
    }
    return all;
}

export function getEbooksPage(opts) {
    return fetchPage('/library/ebooks', opts, 'ebooks');
}

export function getAudiobooksPage(opts) {
    return fetchPage('/library/audiobooks', opts, 'audiobooks');
}

export function getPairsPage(opts) {
    return fetchPage('/library/pairs', opts, 'book pairs');
}

export function getNewPairsPage(opts) {
    return fetchPage('/library/new-pairs', opts, 'new pairs');
}

export function getNewItemsPage(opts) {
    return fetchPage('/library/new-items', opts, 'new items');
}

// ---- Server-driven library browse (issue #120) ----
//
// `getLibraryItemsPage` is the mixed list the Library page renders — each pair
// once plus every unpaired ebook/audiobook (or a tab's slice of it), filtered,
// sorted and paged on the server. Items are `{kind, pair, ebook, audiobook}`
// with the same nested shapes the per-type endpoints return.
// `getLibraryFacets` is the filter-pill options (distinct authors / series
// with counts, scoped to the tab) plus the library-wide tab counts.

// (`q` is carried by pageQuery, which already knows about it.)
function browseQuery({ tab, kind, q, author, series, sort, dir, withQ = false } = {}) {
    const params = new URLSearchParams();
    if (tab) params.set('tab', tab);
    if (kind) params.set('kind', kind);
    if (withQ && q) params.set('q', q);
    if (author) params.set('author', author);
    if (series) params.set('series', series);
    if (sort) params.set('sort', sort);
    if (dir) params.set('dir', dir);
    return params.toString();
}

export async function getLibraryItemsPage(opts = {}) {
    const extra = browseQuery(opts);
    const path = `/library/items${pageQuery(opts)}${extra ? `&${extra}` : ''}`;
    const resp = await fetchWithAuth(`${API_BASE}${path}`);
    return jsonOrThrow(resp, 'Failed to fetch library items');
}

/**
 * Every unpaired ebook and audiobook — for the manual-pairing pickers on the
 * Pairs / Unpaired pages. Bounded to the unpaired set (paged through
 * `tab=unpaired`), instead of downloading the whole library and set-diffing
 * against every pair client-side. Returns `{ebooks, audiobooks}` as plain
 * media objects.
 */
export async function getUnpairedMedia() {
    const [ebookItems, audiobookItems] = await Promise.all([
        fetchAllPages((page) => getLibraryItemsPage({ tab: 'unpaired', kind: 'ebook', page, limit: FETCH_ALL_PAGE_SIZE })),
        fetchAllPages((page) => getLibraryItemsPage({ tab: 'unpaired', kind: 'audiobook', page, limit: FETCH_ALL_PAGE_SIZE })),
    ]);
    return {
        ebooks: ebookItems.map((it) => it.ebook).filter(Boolean),
        audiobooks: audiobookItems.map((it) => it.audiobook).filter(Boolean),
    };
}

export async function getLibraryFacets(opts = {}) {
    const extra = browseQuery(opts);
    const resp = await fetchWithAuth(`${API_BASE}/library/facets${extra ? `?${extra}` : ''}`);
    return jsonOrThrow(resp, 'Failed to fetch library facets');
}

export function getEbooks() {
    return fetchAllPages((page) => getEbooksPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
}

export async function getEbook(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}`);
    return jsonOrThrow(resp, 'Failed to fetch ebook');
}

export function getAudiobooks() {
    return fetchAllPages((page) => getAudiobooksPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
}

export async function getAudiobook(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}`);
    return jsonOrThrow(resp, 'Failed to fetch audiobook');
}

export async function rescanBook(type, id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/${type}s/${id}/rescan`, {
        method: 'POST'
    });
    return jsonOrThrow(resp, `Failed to rescan ${type}`);
}

export function getPairs() {
    return fetchAllPages((page) => getPairsPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
}

/**
 * One pair by id (issue #277).
 *
 * Returns the same `BookPairResponse` the listing's `items` carry, including
 * `sync_map_version`. Use this instead of filtering `getPairs()` — a page that
 * needs one pair should not download the library to find it.
 */
export async function getPair(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}`);
    return jsonOrThrow(resp, 'Failed to fetch pair');
}

export async function createPair(ebookId, audiobookId) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs`, {
        method: 'POST',
        body: JSON.stringify({ ebook_id: ebookId, audiobook_id: audiobookId }),
    });
    return jsonOrThrow(resp, 'Failed to create pair');
}

export async function deletePair(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}`, {
        method: 'DELETE',
    });
    await ensureOk(resp, 'Failed to delete pair');
}

export async function uploadEbook(file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/upload/ebook`, {
        method: 'POST',
        body: formData,
    });
    return jsonOrThrow(resp, 'Upload failed');
}

export async function uploadAudiobook(file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/upload/audiobook`, {
        method: 'POST',
        body: formData,
    });
    return jsonOrThrow(resp, 'Upload failed');
}

export async function uploadEbookCover(id, file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}/cover`, {
        method: 'POST',
        body: formData,
    });
    return jsonOrThrow(resp, 'Failed to upload cover');
}

export async function uploadAudiobookCover(id, file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/cover`, {
        method: 'POST',
        body: formData,
    });
    return jsonOrThrow(resp, 'Failed to upload cover');
}

export async function enrichLibraryFromAbs() {
    const resp = await fetchWithAuth(`${API_BASE}/library/enrich-abs`, { method: 'POST' });
    return jsonOrThrow(resp, `HTTP ${resp.status}`);
}

export async function enrichAudiobookFromAbs(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/enrich-abs`, { method: 'POST' });
    await ensureOk(resp, `HTTP ${resp.status}`);
    // Returns { status, message, book }
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
    return jsonOrThrow(resp, 'Failed to update ebook metadata');
}

export async function updateAudiobookMetadata(bookId, meta) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${bookId}`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(meta),
    });
    return jsonOrThrow(resp, 'Failed to update audiobook metadata');
}

// ============ Chapters ============

export async function getEbookChapters(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}/chapters`);
    return jsonOrThrow(resp, 'Failed to get ebook chapters');
}

export async function getAudiobookChapters(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/chapters`);
    return jsonOrThrow(resp, 'Failed to get audiobook chapters');
}

export async function updateAudiobookChapters(id, chapters) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/chapters`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(chapters)
    });
    return jsonOrThrow(resp, 'Failed to update audiobook chapters');
}

// ============ Match ============

export async function searchMetadata(provider, query, author) {
    const resp = await fetchWithAuth(`${API_BASE}/library/match/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, query, author })
    });
    return jsonOrThrow(resp, 'Failed to search metadata');
}

export async function applyRemoteCover(bookType, bookId, coverUrl) {
    const resp = await fetchWithAuth(`${API_BASE}/library/match/apply-cover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ book_type: bookType, book_id: bookId, cover_url: coverUrl })
    });
    return jsonOrThrow(resp, 'Failed to apply remote cover');
}

// ============ Cleanup ============

export async function getMetadataDiscrepancies() {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs-discrepancies`);
    return jsonOrThrow(resp, 'Failed to get metadata discrepancies');
}

export async function resolveMetadataDiscrepancies(pairId, resolutions) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}/resolve-discrepancies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(resolutions)
    });
    return jsonOrThrow(resp, 'Failed to resolve metadata discrepancies');
}

// ============ New Items / New Pairs Inbox ============

// Whole-list shape `{ebooks: [...], audiobooks: [...]}` preserved for the
// inbox page; the server pages each sub-list independently.
export async function getNewItems() {
    const pages = [];
    for (let page = 1; ; page++) {
        const body = await getNewItemsPage({ page, limit: FETCH_ALL_PAGE_SIZE });
        pages.push(body);
        const more = (sub) => sub.items.length >= FETCH_ALL_PAGE_SIZE && page * FETCH_ALL_PAGE_SIZE < sub.total;
        if (!more(body.ebooks) && !more(body.audiobooks)) break;
    }
    return {
        ebooks: pages.flatMap(p => p.ebooks.items),
        audiobooks: pages.flatMap(p => p.audiobooks.items),
    };
}

export async function acknowledgeNewItems(ebookIds = [], audiobookIds = []) {
    const resp = await fetchWithAuth(`${API_BASE}/library/new-items/acknowledge`, {
        method: 'POST',
        body: JSON.stringify({ ebook_ids: ebookIds, audiobook_ids: audiobookIds }),
    });
    return jsonOrThrow(resp, 'Failed to acknowledge new items');
}

export function getNewPairs() {
    return fetchAllPages((page) => getNewPairsPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
}

export async function acknowledgeNewPairs(pairIds) {
    const resp = await fetchWithAuth(`${API_BASE}/library/new-pairs/acknowledge`, {
        method: 'POST',
        body: JSON.stringify({ pair_ids: pairIds }),
    });
    return jsonOrThrow(resp, 'Failed to acknowledge new pairs');
}

export async function ignoreMetadataDiscrepancies(pairId, fields) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}/ignore-discrepancies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields })
    });
    return jsonOrThrow(resp, 'Failed to ignore metadata discrepancies');
}

// ============ Delete & Verify ============

export async function deleteEbook(id, deleteFile = false) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}?delete_file=${deleteFile}`, {
        method: 'DELETE'
    });
    await ensureOk(resp, 'Failed to delete ebook');
}

export async function deleteAudiobook(id, deleteFile = false) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}?delete_file=${deleteFile}`, {
        method: 'DELETE'
    });
    await ensureOk(resp, 'Failed to delete audiobook');
}

export async function verifyFiles() {
    const resp = await fetchWithAuth(`${API_BASE}/library/verify`);
    return jsonOrThrow(resp, 'Failed to verify files');
}

export async function cleanupOrphans(ebookIds, audiobookIds) {
    const resp = await fetchWithAuth(`${API_BASE}/library/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ebook_ids: ebookIds, audiobook_ids: audiobookIds })
    });
    return jsonOrThrow(resp, 'Failed to cleanup orphaned entries');
}

// ============ Calibre ============

export async function getCalibreStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/library/calibre-status`);
    return jsonOrThrow(resp, 'Failed to check calibre status');
}

// ============ Unsupported Files ============

export async function getUnsupportedFiles() {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported`);
    return jsonOrThrow(resp, 'Failed to load unsupported files');
}

export async function convertUnsupportedFile(id, deleteSource = false) {
    const resp = await fetchWithAuth(
        `${API_BASE}/library/unsupported/${id}/convert?delete_source=${deleteSource}`,
        { method: 'POST' }
    );
    return jsonOrThrow(resp, 'Conversion failed');
}

export async function convertAllUnsupportedFiles(deleteSource = false) {
    const resp = await fetchWithAuth(
        `${API_BASE}/library/unsupported/convert-all?delete_source=${deleteSource}`,
        { method: 'POST' }
    );
    return jsonOrThrow(resp, 'Batch conversion failed');
}

export async function deleteUnsupportedSource(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported/${id}/source`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Delete failed');
}

export async function forceDeleteUnsupportedFile(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported/${id}/force`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Force delete failed');
}

export async function forceDeleteAllUnsupportedFiles() {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported/force-all`, {
        method: 'DELETE',
    });
    return jsonOrThrow(resp, 'Force delete all failed');
}
