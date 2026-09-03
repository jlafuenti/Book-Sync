/**
 * Tandem API Client
 *
 * Handles all HTTP communication with the FastAPI server,
 * including JWT token management.
 */

const API_BASE = '/api';

// Migrate old booksync_* keys on first load
if (!localStorage.getItem('tandem_token') && localStorage.getItem('booksync_token')) {
    localStorage.setItem('tandem_token', localStorage.getItem('booksync_token'));
    localStorage.setItem('tandem_refresh', localStorage.getItem('booksync_refresh'));
    localStorage.removeItem('booksync_token');
    localStorage.removeItem('booksync_refresh');
}

let accessToken = localStorage.getItem('tandem_token');
let refreshToken = localStorage.getItem('tandem_refresh');

function setTokens(access, refresh) {
    accessToken = access;
    refreshToken = refresh;
    localStorage.setItem('tandem_token', access);
    localStorage.setItem('tandem_refresh', refresh);
}

function clearTokens() {
    accessToken = null;
    refreshToken = null;
    localStorage.removeItem('tandem_token');
    localStorage.removeItem('tandem_refresh');
    // Media tokens are minted against the session that asked for them, and
    // logout ends that session server-side -- so every cached one is already
    // dead. Logout followed by login is an SPA transition with no page reload,
    // so without this the next session keeps serving them and every cover and
    // audio request 401s for up to 14 minutes (issue #284). Before issue #250
    // it was the `token_version` bump that killed them; now it is the session's
    // `sid`, but the cache is just as stale either way.
    mediaTokenCache.clear();
}

// ============ Device Identity (issue #54 — multi-device conflict resolution) ============
//
// A stable per-install id/name sent with every bookmark/progress write so the
// server can attribute writes to a device and detect stale-write conflicts
// (see the `captured_at` handling in updateBookmark/updateProgress below).

function uuidv4Fallback() {
    // RFC 4122 v4 UUID, for browsers/environments without crypto.randomUUID
    // (e.g. non-HTTPS contexts, which disable the Crypto API's randomUUID).
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
}

export function getDeviceId() {
    let id = localStorage.getItem('tandem_device_id');
    if (!id) {
        id = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
            ? crypto.randomUUID()
            : uuidv4Fallback();
        localStorage.setItem('tandem_device_id', id);
    }
    return id;
}

function deriveDeviceName() {
    const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
    let browser = 'Browser';
    if (/Edg\//.test(ua)) browser = 'Edge';
    else if (/OPR\//.test(ua)) browser = 'Opera';
    else if (/Firefox\//.test(ua)) browser = 'Firefox';
    else if (/Chrome\//.test(ua) && !/Chromium/.test(ua)) browser = 'Chrome';
    else if (/Safari\//.test(ua) && !/Chrome/.test(ua)) browser = 'Safari';
    return `Web · ${browser}`;
}

export function getDeviceName() {
    let name = localStorage.getItem('tandem_device_name');
    if (!name) {
        name = deriveDeviceName();
        localStorage.setItem('tandem_device_name', name);
    }
    return name;
}

// Short-lived tokens scoped to one cover/audiobook resource, minted via
// /api/auth/media-token. Used instead of the long-lived access token on URLs
// that can't carry an Authorization header (img tags, the <audio> element,
// Cast SDK media URLs) -- see issue #50.
const mediaTokenCache = new Map(); // "cover:filename" -> { token, expiresAt }
const MEDIA_TOKEN_BUFFER_MS = 60_000;

// Coalescing (issue #269). A library page commits 50 covers at once and every
// one of them used to await its own GET — 50 round-trips through a ~6-
// connection-per-host queue, and another 50 on each infinite-scroll append.
// Requests raised in the same tick are collected here and flushed together
// against /auth/media-token/batch, so N covers cost one round-trip. The pending
// map doubles as the in-flight map: two callers for the same resource share one
// promise instead of racing two mints for the same token.
const mediaTokenQueue = new Map();     // key -> { resourceType, resourceId, resolve, reject }
const mediaTokenInFlight = new Map();  // key -> Promise<string>
let mediaTokenFlushHandle = null;

async function mintMediaToken(resourceType, resourceId) {
    const resp = await fetchWithAuth(
        `${API_BASE}/auth/media-token?resource_type=${resourceType}&resource_id=${encodeURIComponent(resourceId)}`
    );
    if (!resp.ok) throw new Error('Failed to get media token');
    const { token, expires_in } = await resp.json();
    mediaTokenCache.set(`${resourceType}:${resourceId}`, {
        token, expiresAt: Date.now() + expires_in * 1000,
    });
    return token;
}

async function flushMediaTokenQueue() {
    mediaTokenFlushHandle = null;
    const batch = [...mediaTokenQueue.entries()];
    mediaTokenQueue.clear();
    if (!batch.length) return;

    // One resource is not a batch: the GET is a smaller request and keeps a
    // lone cover (a detail page, the player) on the cheaper path.
    let tokens = {};
    if (batch.length > 1) {
        try {
            const resp = await fetchWithAuth(`${API_BASE}/auth/media-token/batch`, {
                method: 'POST',
                body: JSON.stringify({
                    resources: batch.map(([, e]) => ({
                        resource_type: e.resourceType, resource_id: e.resourceId,
                    })),
                }),
            });
            if (resp.ok) {
                const data = await resp.json();
                tokens = data.tokens || {};
                const expiresAt = Date.now() + data.expires_in * 1000;
                for (const [k, t] of Object.entries(tokens)) {
                    mediaTokenCache.set(k, { token: t, expiresAt });
                }
            }
        } catch {
            // Batch unavailable (offline, an old server without the endpoint):
            // every waiter falls through to its own GET below.
        }
    }

    // Anything the batch did not answer for — a failed call, an older server,
    // or a key the server declined — still gets its own mint, in parallel.
    await Promise.all(batch.map(async ([key, entry]) => {
        try {
            const token = tokens[key] ?? await mintMediaToken(entry.resourceType, entry.resourceId);
            mediaTokenInFlight.delete(key);
            entry.resolve(token);
        } catch (err) {
            mediaTokenInFlight.delete(key);
            entry.reject(err);
        }
    }));
}

function getMediaToken(resourceType, resourceId) {
    const key = `${resourceType}:${resourceId}`;
    const cached = mediaTokenCache.get(key);
    if (cached && cached.expiresAt - Date.now() > MEDIA_TOKEN_BUFFER_MS) {
        return Promise.resolve(cached.token);
    }
    const inFlight = mediaTokenInFlight.get(key);
    if (inFlight) return inFlight;

    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    mediaTokenInFlight.set(key, promise);
    mediaTokenQueue.set(key, { resourceType, resourceId, resolve, reject });
    if (!mediaTokenFlushHandle) mediaTokenFlushHandle = setTimeout(flushMediaTokenQueue, 0);
    return promise;
}

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

// One refresh at a time (issue #268). Every authenticated call goes through
// fetchWithAuth, and the pages fire several at once — the Home page opens with
// five. When the 24h access token expires they all 401 together, and without
// this each one POSTed its own /auth/refresh and raced the others into
// localStorage.
//
// Since issue #250 a refresh token names a session (`jti`) and /auth/refresh
// re-issues *for that session* rather than replacing it, so a second refresh
// with the token this one is about to supersede still succeeds — the session,
// not the token, is what a logout ends. Keep the single-flight anyway: it is
// what stops five racing writes to localStorage, and it is the guarantee that
// would have to be replaced first if the server ever did start retiring the
// presented token.
//
// This is the same single-flight the Android TokenAuthenticator does (#143).
let refreshInFlight = null;

/**
 * Refresh the session, collapsing concurrent callers onto one request.
 *
 * @returns {Promise<string|null>} the new access token, or null if the session
 *   is over — in which case the tokens are cleared and `tandem:unauthorized`
 *   has been dispatched.
 */
function refreshSession() {
    if (refreshInFlight) return refreshInFlight;

    refreshInFlight = (async () => {
        try {
            const resp = await fetch(`${API_BASE}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // device_id matters only for a refresh token minted before
                // per-device sessions existed: the server upgrades it onto a
                // session, and this is what names that session (issue #250).
                body: JSON.stringify({
                    refresh_token: refreshToken,
                    device_id: getDeviceId(),
                }),
            });
            if (!resp.ok) {
                endSession();
                return null;
            }
            const data = await resp.json();
            setTokens(data.access_token, data.refresh_token);
            return data.access_token;
        } catch {
            // A network failure is not an expired session: leave the tokens
            // alone so the next attempt can succeed, and let the caller see the
            // original 401.
            return null;
        } finally {
            refreshInFlight = null;
        }
    })();

    return refreshInFlight;
}

/**
 * End the session without reloading the page (issue #268).
 *
 * This used to be `window.location.href = '/login'`. That reload fired from
 * anywhere, including a background position heartbeat, and tore down an open
 * reader together with its pending save and any active playback. The session is
 * over either way; the reload is what cost the unsaved position.
 *
 * `App.jsx` listens for this and drops to LoginPage via React state, the same
 * shape as the `tandem:password-reset-required` event next to it (#209). It is
 * the only consumer, and it always registers the listener — so there is no
 * fallback reload here, and a test pins that no navigation ever returns.
 */
function endSession() {
    clearTokens();
    window.dispatchEvent(new CustomEvent('tandem:unauthorized'));
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

    // If 401, refresh once and retry (issue #268).
    if (response.status === 401 && refreshToken && !String(url).includes('/auth/refresh')) {
        const attempted = accessToken;
        const fresh = await refreshSession();

        if (fresh) {
            headers['Authorization'] = `Bearer ${fresh}`;
            response = await fetch(url, { ...options, headers });
        } else if (attempted !== accessToken && accessToken) {
            // Someone else's refresh landed while this call was in flight.
            headers['Authorization'] = `Bearer ${accessToken}`;
            response = await fetch(url, { ...options, headers });
        }
    }

    // The server refuses everything except /auth/me, /auth/change-password and
    // /auth/logout while must_reset_password is set (issue #209). App.jsx gates on
    // the flag from the getMe() it runs at mount; this is the backstop for any
    // call that goes out before or alongside that, and it turns an unexplained
    // failure into a route to the reset screen.
    //
    // Note it is a backstop, not the main path, and specifically NOT the
    // "admin resets you mid-session" case: routers/users.py bumps token_version in
    // the same transaction that sets the flag, so an open tab gets 401 and goes
    // through the refresh/relogin path above, never reaching this.
    //
    // Cloned, because the caller still needs to read this body: several callers
    // surface `detail` as the error message.
    if (response.status === 403) {
        try {
            const body = await response.clone().json();
            if (body?.detail === 'password_reset_required') {
                window.dispatchEvent(new CustomEvent('tandem:password-reset-required'));
            }
        } catch {
            // A 403 from the proxy rather than the app is HTML, not JSON.
            // Nothing to sniff — leave it entirely to the caller.
        }
    }

    return response;
}

// ============ Auth ============

/**
 * Turn a failed response into a message a user can act on (issue #211).
 *
 * `(await resp.json()).detail` is fine while the app answers, but the two
 * unauthenticated endpoints below are the ones people hit when it doesn't: an
 * nginx 502/504 page is HTML, `.json()` throws a SyntaxError, and that replaces
 * the real failure — the login form said "Unexpected token '<'" when the server
 * was simply down. Read the body as text, parse it only if it parses, and fall
 * back to the status otherwise. Never surface the body itself: it is untrusted
 * markup, and the status is what actually tells the user what to do.
 */
async function errorMessage(resp, fallback) {
    let detail;
    try {
        detail = JSON.parse(await resp.text())?.detail;
    } catch {
        // Not JSON (a proxy error page), or the body could not be read.
    }
    if (typeof detail === 'string' && detail) return detail;
    return `${fallback} (${resp.status})`;
}

export async function login(username, password) {
    const resp = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The session this opens is named after the browser, so signing out
        // here signs out only here (issue #250).
        body: JSON.stringify({ username, password, device_id: getDeviceId() }),
    });
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Login failed'));
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
    if (!resp.ok) throw new Error(await errorMessage(resp, 'Registration failed'));
    return resp.json();
}

export async function getMe() {
    const resp = await fetchWithAuth(`${API_BASE}/auth/me`);
    if (!resp.ok) return null;
    return resp.json();
}

export async function logout() {
    // Best-effort: invalidate server-side tokens, but always clear local
    // tokens even if the request fails (e.g. offline, token already expired).
    //
    // The refresh token goes in the body to name the session to end (issue
    // #250) — this browser's, not the phone's, which is usually mid-book with
    // positions it has not pushed yet. The server falls back to the session
    // named by the access token, so an old client is not signed out everywhere
    // by accident either; sending it explicitly just means the call still names
    // the right session when the access token is the thing that has expired.
    const presented = refreshToken;
    try {
        await fetchWithAuth(`${API_BASE}/auth/logout`, {
            method: 'POST',
            body: JSON.stringify({ refresh_token: presented }),
        });
    } catch {
        // ignore — local logout must still succeed
    }
    clearTokens();
}

export function isLoggedIn() {
    return !!accessToken;
}

export async function updateMe(data) {
    const resp = await fetchWithAuth(`${API_BASE}/auth/me`, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error('Failed to update profile');
    return resp.json();
}

export function getAccessToken() {
    return accessToken;
}

export async function changePassword(oldPassword, newPassword) {
    const resp = await fetchWithAuth(`${API_BASE}/auth/change-password`, {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to change password');
    return resp.json();
}

// ============ User Management (Admin) ============

export async function getUsers(filter) {
    const params = filter ? `?filter=${filter}` : '';
    const resp = await fetchWithAuth(`${API_BASE}/users/${params}`);
    if (!resp.ok) throw new Error('Failed to fetch users');
    return resp.json();
}

export async function createUser(data) {
    const resp = await fetchWithAuth(`${API_BASE}/users/`, {
        method: 'POST',
        body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to create user');
    return resp.json();
}

export async function updateUser(id, data) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to update user');
    return resp.json();
}

export async function approveUser(id) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}/approve`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to approve user');
    return resp.json();
}

export async function resetUserPassword(id, newPassword) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}/reset-password`, {
        method: 'POST',
        body: JSON.stringify({ new_password: newPassword }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to reset password');
    return resp.json();
}

export async function deleteUser(id) {
    const resp = await fetchWithAuth(`${API_BASE}/users/${id}`, {
        method: 'DELETE',
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to delete user');
    return resp.json();
}

export async function getAuditLog(page = 1, limit = 50, action, userId) {
    let params = `?page=${page}&limit=${limit}`;
    if (action) params += `&action=${action}`;
    if (userId) params += `&user_id=${userId}`;
    const resp = await fetchWithAuth(`${API_BASE}/users/audit-log${params}`);
    if (!resp.ok) throw new Error('Failed to fetch audit log');
    return resp.json();
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

// ---- Paginated list endpoints (issue #48) ----
//
// The server returns `{items, total, page, limit}` (limit ≤ 500, default 100)
// and accepts `q` for a server-side title/author/series search. The `*Page`
// functions expose one page for a paged UI; the legacy whole-list functions
// (`getEbooks()`, `getPairs()`, ...) keep their array return shape by walking
// every page, for the pages that still assemble lists locally (Home,
// Continue, Series, Transcription, System). The Library page is server-driven
// (issue #120, `getLibraryItemsPage` below) and no longer uses them.

const FETCH_ALL_PAGE_SIZE = 500;

function pageQuery({ page = 1, limit = 100, q } = {}) {
    const params = new URLSearchParams({ page: String(page), limit: String(limit) });
    if (q) params.set('q', q);
    return `?${params.toString()}`;
}

async function fetchPage(path, opts, what) {
    const resp = await fetchWithAuth(`${API_BASE}${path}${pageQuery(opts)}`);
    if (!resp.ok) throw new Error(`Failed to fetch ${what}`);
    return resp.json();
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
    if (!resp.ok) throw new Error('Failed to fetch library items');
    return resp.json();
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
    if (!resp.ok) throw new Error('Failed to fetch library facets');
    return resp.json();
}

export function getEbooks() {
    return fetchAllPages((page) => getEbooksPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
}

export async function getEbook(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}`);
    if (!resp.ok) throw new Error('Failed to fetch ebook');
    return resp.json();
}

export function getAudiobooks() {
    return fetchAllPages((page) => getAudiobooksPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
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


export function getPairs() {
    return fetchAllPages((page) => getPairsPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
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

// ============ Transcription Queue ============

export async function getTranscriptionQueue() {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue`);
    if (!resp.ok) throw new Error('Failed to fetch transcription queue');
    return resp.json();
}

export async function addToQueue(pairIds) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/batch`, {
        method: 'POST',
        body: JSON.stringify({ pair_ids: pairIds }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to add to queue');
    return resp.json();
}

export async function removeFromQueue(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}`, {
        method: 'DELETE',
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to remove from queue');
    return resp.json();
}

export async function updateQueuePriority(itemId, priority) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}/priority`, {
        method: 'PUT',
        body: JSON.stringify({ priority }),
    });
    if (!resp.ok) throw new Error('Failed to update priority');
    return resp.json();
}

export async function getQueueHistory(limit = 50, offset = 0) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/history?limit=${limit}&offset=${offset}`);
    if (!resp.ok) throw new Error('Failed to fetch queue history');
    return resp.json();
}

// Dispatch a queued item immediately, ignoring the off-hours window (#106).
export async function runQueueItemNow(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}/run-now`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to start item');
    return resp.json();
}

// Retry a failed or cancelled history item (#247). The server queues a *new*
// pending row for the same pair — the failed row stays in History.
export async function requeueQueueItem(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/queue/${itemId}/requeue`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to retry item');
    return resp.json();
}

// Current off-hours window state, for the queue page banner.
export async function getOffHoursStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/transcription/offhours`);
    if (!resp.ok) throw new Error('Failed to fetch off-hours status');
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

// ============ Backups (issue #60) ============

export async function getBackupStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backup`);
    if (!resp.ok) throw new Error('Failed to fetch backup status');
    return resp.json();
}

export async function listBackups() {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups`);
    if (!resp.ok) throw new Error('Failed to list backups');
    return resp.json();
}

export async function restoreBackup(backupId) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backup_id: backupId, confirm: true }),
    });
    if (!resp.ok) {
        let detail = 'Failed to restore backup';
        try { detail = (await resp.json()).detail || detail; } catch { /* non-JSON */ }
        throw new Error(detail);
    }
    return resp.json();
}

export async function createBackup(label) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: label || null }),
    });
    if (!resp.ok) {
        let detail = 'Failed to create backup';
        try { detail = (await resp.json()).detail || detail; } catch { /* non-JSON */ }
        throw new Error(detail);
    }
    return resp.json();
}

export async function deleteBackup(backupId) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups/${encodeURIComponent(backupId)}`, {
        method: 'DELETE',
    });
    if (!resp.ok) {
        let detail = 'Failed to delete backup';
        try { detail = (await resp.json()).detail || detail; } catch { /* non-JSON */ }
        throw new Error(detail);
    }
    return resp.json();
}

// Fetch the .dump with the auth header, then trigger a browser download of the
// blob (an <a href> can't carry the Authorization header).
export async function downloadBackup(backupId) {
    const resp = await fetchWithAuth(`${API_BASE}/stats/backups/${encodeURIComponent(backupId)}/download`);
    if (!resp.ok) throw new Error('Failed to download backup');
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

// ============ Progress ============

export async function getAllProgress() {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress`);
    if (!resp.ok) throw new Error('Failed to fetch all progress');
    return resp.json();
}

export async function getProgress(mediaType, mediaId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress/${mediaType}/${mediaId}`);
    // 204 = this user has no recorded position for this media. The endpoint used
    // to INSERT a blank row on a miss and answer 200 with it, which is how one
    // book could end up with two rows and 500 forever after (issue #64). There
    // is no body to parse now — null is the position, not an error.
    if (resp.status === 204) return null;
    if (!resp.ok) throw new Error('Failed to fetch progress');
    return resp.json();
}

// There is no `updateProgress` — `user_progress` is a read-only projection of
// the canonical record (issue #102). Write with `updatePosition` below.

export async function resetPairProgress(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/progress/pair/${pairId}`, {
        method: 'DELETE',
    });
    if (!resp.ok) throw new Error('Failed to reset pair progress');
    return resp.json();
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
    if (!resp.ok) throw new Error('Failed to fetch position');
    return resp.json();
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
    if (!resp.ok) throw new Error('Failed to update position');
    return resp.json();
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
    if (!resp.ok) throw new Error('Failed to reset position');
    return resp.json();
}

// ============ Bookmarks ============
//
// Only the history log remains — the bookmark GET/PUT went with the legacy
// adapters (issue #102). Read and write positions with getPosition/updatePosition.

export async function getBookmarkLog(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/sync/bookmark/${pairId}/log`);
    if (!resp.ok) throw new Error('Failed to fetch bookmark log');
    return resp.json();
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
    if (!accessToken) return;
    try {
        fetch(`${API_BASE}/sync/position/${scope}/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${accessToken}`,
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

// ============ File Access ============

export async function fetchEbookBlob(ebookId) {
    const resp = await fetchWithAuth(`${API_BASE}/files/ebook/${ebookId}`);
    if (!resp.ok) throw new Error('Failed to fetch ebook file');
    return resp.arrayBuffer();
}

export async function getAudiobookStreamUrl(audiobookId) {
    const token = await getMediaToken('audiobook', String(audiobookId));
    return `${API_BASE}/files/audiobook/${audiobookId}?token=${token}`;
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
    // Surface the server's `detail` — settings validation (e.g. an invalid
    // off-hours window) explains exactly what's wrong, and a generic message
    // would throw that away.
    return _jsonOrThrow(resp, 'Failed to update settings');
}

export async function testRemoteConnection(url, key = '') {
    if (!url) throw new Error("URL is required");
    // We ping via the backend to avoid CORS and VPN local-routing issues
    // where the user's browser cannot reach the Jetson directly.
    // POST, not GET: the Jetson key is long-lived and not resource-scoped, and a
    // query string lands in the access log. One was found there (issue #284).
    const resp = await fetchWithAuth(`${API_BASE}/settings/test-remote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, key }),
    });

    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try {
            const errBody = await resp.json();
            if (errBody.detail) msg = errBody.detail;
        } catch(e) {}
        throw new Error(msg);
    }
    return resp.json();
}

export async function generateTranscriptionRemoteKey() {
    const resp = await fetchWithAuth(`${API_BASE}/settings/transcription-remote-key/generate`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error('Failed to generate key');
    return resp.json();
}

export async function testAbsConnection(url, token = '') {
    if (!url) throw new Error("URL is required");
    const resp = await fetchWithAuth(`${API_BASE}/settings/test-abs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, token }),
    });
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try {
            const errBody = await resp.json();
            if (errBody.detail) msg = errBody.detail;
        } catch(e) {}
        throw new Error(msg);
    }
    return resp.json();
}

export async function testHardcoverConnection(token = '') {
    const resp = await fetchWithAuth(`${API_BASE}/settings/test-hardcover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
    });
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try {
            const errBody = await resp.json();
            if (errBody.detail) msg = errBody.detail;
        } catch(e) {}
        throw new Error(msg);
    }
    return resp.json();
}

export async function enrichLibraryFromAbs() {
    const resp = await fetchWithAuth(`${API_BASE}/library/enrich-abs`, { method: 'POST' });
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const e = await resp.json(); if (e.detail) msg = e.detail; } catch(e) {}
        throw new Error(msg);
    }
    return resp.json();
}

export async function enrichAudiobookFromAbs(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}/enrich-abs`, { method: 'POST' });
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const e = await resp.json(); if (e.detail) msg = e.detail; } catch(e) {}
        throw new Error(msg);
    }
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
    if (!resp.ok) throw new Error('Failed to acknowledge new items');
    return resp.json();
}

export function getNewPairs() {
    return fetchAllPages((page) => getNewPairsPage({ page, limit: FETCH_ALL_PAGE_SIZE }));
}

export async function acknowledgeNewPairs(pairIds) {
    const resp = await fetchWithAuth(`${API_BASE}/library/new-pairs/acknowledge`, {
        method: 'POST',
        body: JSON.stringify({ pair_ids: pairIds }),
    });
    if (!resp.ok) throw new Error('Failed to acknowledge new pairs');
    return resp.json();
}

export async function ignoreMetadataDiscrepancies(pairId, fields) {
    const resp = await fetchWithAuth(`${API_BASE}/library/pairs/${pairId}/ignore-discrepancies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields })
    });
    if (!resp.ok) throw new Error('Failed to ignore metadata discrepancies');
    return resp.json();
}

// ============ Delete & Verify ============

export async function deleteEbook(id, deleteFile = false) {
    const resp = await fetchWithAuth(`${API_BASE}/library/ebooks/${id}?delete_file=${deleteFile}`, {
        method: 'DELETE'
    });
    if (!resp.ok) throw new Error('Failed to delete ebook');
}

export async function deleteAudiobook(id, deleteFile = false) {
    const resp = await fetchWithAuth(`${API_BASE}/library/audiobooks/${id}?delete_file=${deleteFile}`, {
        method: 'DELETE'
    });
    if (!resp.ok) throw new Error('Failed to delete audiobook');
}

export async function verifyFiles() {
    const resp = await fetchWithAuth(`${API_BASE}/library/verify`);
    if (!resp.ok) throw new Error('Failed to verify files');
    return resp.json();
}

export async function cleanupOrphans(ebookIds, audiobookIds) {
    const resp = await fetchWithAuth(`${API_BASE}/library/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ebook_ids: ebookIds, audiobook_ids: audiobookIds })
    });
    if (!resp.ok) throw new Error('Failed to cleanup orphaned entries');
    return resp.json();
}

// ============ Calibre ============

export async function getCalibreStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/library/calibre-status`);
    if (!resp.ok) throw new Error('Failed to check calibre status');
    return resp.json();
}

// ============ Unsupported Files ============

export async function getUnsupportedFiles() {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported`);
    if (!resp.ok) throw new Error('Failed to load unsupported files');
    return resp.json();
}

export async function convertUnsupportedFile(id, deleteSource = false) {
    const resp = await fetchWithAuth(
        `${API_BASE}/library/unsupported/${id}/convert?delete_source=${deleteSource}`,
        { method: 'POST' }
    );
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || 'Conversion failed');
    }
    return resp.json();
}

export async function convertAllUnsupportedFiles(deleteSource = false) {
    const resp = await fetchWithAuth(
        `${API_BASE}/library/unsupported/convert-all?delete_source=${deleteSource}`,
        { method: 'POST' }
    );
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || 'Batch conversion failed');
    }
    return resp.json();
}

export async function deleteUnsupportedSource(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported/${id}/source`, {
        method: 'DELETE',
    });
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || 'Delete failed');
    }
    return resp.json();
}

export async function forceDeleteUnsupportedFile(id) {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported/${id}/force`, {
        method: 'DELETE',
    });
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || 'Force delete failed');
    }
    return resp.json();
}

export async function forceDeleteAllUnsupportedFiles() {
    const resp = await fetchWithAuth(`${API_BASE}/library/unsupported/force-all`, {
        method: 'DELETE',
    });
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || 'Force delete all failed');
    }
    return resp.json();
}

/* ── Import Sources ──────────────────────────────────────────────────── */

async function _jsonOrThrow(resp, fallback) {
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || fallback);
    }
    return resp.json();
}

export async function listImportSources() {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources`);
    return _jsonOrThrow(resp, 'Failed to list import sources');
}

export async function updateImportSourceConfig(sourceKey, config) {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources/${sourceKey}/config`, {
        method: 'PUT',
        body: JSON.stringify(config),
    });
    return _jsonOrThrow(resp, 'Failed to update source config');
}

export async function triggerImportSync(sourceKey) {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources/${sourceKey}/sync`, {
        method: 'POST',
    });
    return _jsonOrThrow(resp, 'Failed to trigger sync');
}

export async function getImportJobs(sourceKey) {
    const resp = await fetchWithAuth(`${API_BASE}/import/sources/${sourceKey}/jobs`);
    return _jsonOrThrow(resp, 'Failed to fetch jobs');
}

export async function audibleLoginStart() {
    const resp = await fetchWithAuth(`${API_BASE}/import/audible/login/start`, {
        method: 'POST',
        body: JSON.stringify({}),
    });
    return _jsonOrThrow(resp, 'Failed to start Audible login');
}

export async function audibleLoginComplete(stateToken, responseUrl) {
    const resp = await fetchWithAuth(`${API_BASE}/import/audible/login/complete`, {
        method: 'POST',
        body: JSON.stringify({ state_token: stateToken, response_url: responseUrl }),
    });
    return _jsonOrThrow(resp, 'Audible login failed');
}

export async function audibleDisconnect() {
    const resp = await fetchWithAuth(`${API_BASE}/import/audible/disconnect`, {
        method: 'POST',
    });
    return _jsonOrThrow(resp, 'Failed to disconnect Audible');
}

export async function uploadAcsm(file) {
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/upload`, {
        method: 'POST',
        body: fd,
    });
    return _jsonOrThrow(resp, 'Upload failed');
}

export async function acsmAuthorizationStatus() {
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/authorization`);
    return _jsonOrThrow(resp, 'Failed to check authorization');
}

export async function acsmAuthorize({ mode, email = '', password = '' }) {
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/authorize`, {
        method: 'POST',
        body: JSON.stringify({ mode, email, password }),
    });
    return _jsonOrThrow(resp, 'Authorization failed');
}

export async function acsmDeauthorize() {
    const resp = await fetchWithAuth(`${API_BASE}/import/acsm/deauthorize`, {
        method: 'POST',
    });
    return _jsonOrThrow(resp, 'Failed to revoke authorization');
}

// ============ Troubleshoot Library ============

export async function getLibraryIssues() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/issues`);
    return _jsonOrThrow(resp, 'Failed to load library issues');
}

export async function startLibraryScan() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/scan`, { method: 'POST' });
    return _jsonOrThrow(resp, 'Failed to start scan');
}

export async function getLibraryScanProgress() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/scan/progress`);
    return _jsonOrThrow(resp, 'Failed to get scan progress');
}

export async function cancelLibraryScan() {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/scan/cancel`, { method: 'POST' });
    return _jsonOrThrow(resp, 'Failed to cancel scan');
}

export async function bulkDeleteIssues(items, deleteFile = true) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/bulk-delete?delete_file=${deleteFile}`, {
        method: 'POST',
        body: JSON.stringify({ items }),
    });
    return _jsonOrThrow(resp, 'Bulk delete failed');
}

export async function replaceLibraryFile(itemType, itemId, file) {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/replace/${itemType}/${itemId}`, {
        method: 'POST',
        body: formData,
    });
    return _jsonOrThrow(resp, 'Replace failed');
}

export async function requeuePair(pairId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/requeue/${pairId}`, { method: 'POST' });
    return _jsonOrThrow(resp, 'Requeue failed');
}

export async function repairChapterEncoding(itemId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/repair-chapter-encoding/${itemId}`, { method: 'POST' });
    return _jsonOrThrow(resp, 'Repair failed');
}

export async function bulkRepairChapterEncoding(itemIds) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/bulk-repair-chapter-encoding`, {
        method: 'POST',
        body: JSON.stringify({ item_ids: itemIds }),
    });
    return _jsonOrThrow(resp, 'Bulk repair failed');
}

export async function dismissFailedAcsm(filename) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/acsm-dismiss`, {
        method: 'POST',
        body: JSON.stringify({ filename }),
    });
    return _jsonOrThrow(resp, 'Dismiss failed');
}

// Multi-file audiobook folders (issue #63): flagged by the library scan, not
// imported. Dismiss hides one until its contents change; remove-tracks deletes
// the AudioBook rows imported one-per-track before detection existed (files
// stay on disk for merging in Audiobookshelf).
export async function dismissMultiFileFolder(folderId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/multi-file/${folderId}/dismiss`, {
        method: 'POST',
    });
    return _jsonOrThrow(resp, 'Dismiss failed');
}

export async function removeMultiFileTracks(folderId) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/multi-file/${folderId}/remove-tracks`, {
        method: 'POST',
    });
    return _jsonOrThrow(resp, 'Removing imported tracks failed');
}

export async function deleteOrphanCovers(filenames) {
    const resp = await fetchWithAuth(`${API_BASE}/troubleshoot/delete-orphan-covers`, {
        method: 'POST',
        body: JSON.stringify({ filenames }),
    });
    return _jsonOrThrow(resp, 'Delete failed');
}
