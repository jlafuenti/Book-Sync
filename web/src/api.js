/**
 * Tandem API Client — the public surface.
 *
 * The implementation lives in `src/api/`, one module per domain. This file is
 * the barrel: everything a page could import from `'../api'` before the split
 * is still exported here, so no page needed an import change and every
 * `vi.mock('../api', …)` in the test suite keeps working (issue #275).
 *
 *   api/http.js           transport, session, and the error contract
 *   api/auth.js           sign in/out, the signed-in account
 *   api/users.js          user management (admin)
 *   api/library.js        books, pairs, uploads, metadata, cleanup
 *   api/transcription.js  transcription and its queue
 *   api/sync.js           sync maps, progress, the canonical position
 *   api/files.js          media tokens, covers, ebook/audio bytes
 *   api/stats.js          disk usage, backups
 *   api/settings.js       server settings, connection tests
 *   api/importSources.js  Audible, ACSM
 *   api/troubleshoot.js   library issues and repairs
 *
 * `api/http.js` holds the module-level session state — the tokens, the media
 * token cache, and the single-flight `refreshSession` (issue #143/#268). Every
 * other module imports it, so there is exactly one of each in the graph: five
 * requests 401ing together still cost one refresh, which is the guarantee that
 * would have to be replaced first if the server ever started rotating refresh
 * tokens.
 *
 * Only http.js is re-exported by name rather than wholesale. Its transport
 * internals — `setTokens`, `clearTokens`, `fetchWithAuth`, `errorMessage`,
 * `getMediaToken` — are exported for the sibling modules, not for pages, and
 * `export *` would quietly widen the app's API surface to include them.
 */

export {
    getDeviceId, getDeviceName, isLoggedIn, getAccessToken, ensureOk, jsonOrThrow,
} from './api/http';

export * from './api/auth';
export * from './api/users';
export * from './api/library';
export * from './api/transcription';
export * from './api/sync';
export * from './api/files';
export * from './api/stats';
export * from './api/settings';
export * from './api/importSources';
export * from './api/troubleshoot';
