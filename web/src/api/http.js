/**
 * Tandem API Client
 *
 * Handles all HTTP communication with the FastAPI server,
 * including JWT token management.
 */

export const API_BASE = '/api';

// Migrate old booksync_* keys on first load
if (!localStorage.getItem('tandem_token') && localStorage.getItem('booksync_token')) {
    localStorage.setItem('tandem_token', localStorage.getItem('booksync_token'));
    localStorage.setItem('tandem_refresh', localStorage.getItem('booksync_refresh'));
    localStorage.removeItem('booksync_token');
    localStorage.removeItem('booksync_refresh');
}

let accessToken = localStorage.getItem('tandem_token');
let refreshToken = localStorage.getItem('tandem_refresh');

export function setTokens(access, refresh) {
    accessToken = access;
    refreshToken = refresh;
    localStorage.setItem('tandem_token', access);
    localStorage.setItem('tandem_refresh', refresh);
}

export function clearTokens() {
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
    await ensureOk(resp, 'Failed to get media token');
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

export function getMediaToken(resourceType, resourceId) {
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

export async function fetchWithAuth(url, options = {}) {
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

/**
 * The error contract (issue #275).
 *
 * Every failed response becomes an `Error` carrying the server's `detail` when
 * it has one, and the caller's own fallback string when it does not. This used
 * to be copied inline at ~70 call sites instead of called, so whether a user
 * saw the server's explanation or a fixed English string depended on which
 * function the page happened to reach for: `updateMe`, `getUsers`,
 * `getAuditLog` and `getSyncMap` discarded `detail` while their immediate
 * neighbours surfaced it.
 *
 * The body is read defensively — a 502 from the proxy is HTML and `.json()`
 * throws a SyntaxError on it, which would replace the real failure with a parse
 * error. The two unauthenticated endpoints (`login`, `register`) deliberately
 * use `errorMessage` below instead, which also appends the status: those are
 * the calls people make when the app is *down*, and which server failed is the
 * only actionable thing left.
 *
 * These are the single seam for anything the client should later do to every
 * request — cancellation, retry, telemetry, an offline-aware message.
 */
export async function ensureOk(resp, fallback) {
    if (resp.ok) return resp;
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || fallback);
}

/** `ensureOk`, then the parsed body — the shape most endpoints want. */
export async function jsonOrThrow(resp, fallback) {
    await ensureOk(resp, fallback);
    return resp.json();
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
export async function errorMessage(resp, fallback) {
    let detail;
    try {
        detail = JSON.parse(await resp.text())?.detail;
    } catch {
        // Not JSON (a proxy error page), or the body could not be read.
    }
    if (typeof detail === 'string' && detail) return detail;
    return `${fallback} (${resp.status})`;
}

export function isLoggedIn() {
    return !!accessToken;
}

export function getAccessToken() {
    return accessToken;
}

/**
 * The refresh token, for the one caller outside this module that needs it:
 * `logout` names the session to end by presenting it (issue #250).
 *
 * An accessor rather than an exported `let`. The token is reassigned here on
 * every refresh, and a module that imported the binding directly would be
 * reading a value this module owns — the kind of shared mutable state the
 * split was meant to remove, not spread.
 */
export function getRefreshToken() {
    return refreshToken;
}
