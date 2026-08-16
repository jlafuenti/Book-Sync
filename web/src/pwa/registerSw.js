/**
 * Service-worker registration (issue #62). See docs/web-pwa.md.
 *
 * The worker itself is generated at build time by vite-plugin-pwa (config in
 * vite.config.js): it precaches the built app shell — index.html, the hashed
 * JS/CSS chunks, icons — and serves navigations from that cache so an
 * installed Tandem opens instantly and still reaches the login/home screen
 * offline. Everything under /api/ is deliberately network-only: it is
 * bearer-token JSON or short-lived-token media, none of which is safe or
 * useful to cache.
 *
 * Registration is gated on a production build so the dev server (HMR) and
 * vitest never see a worker, and on browser support. `virtual:pwa-register`
 * is the plugin's tiny registration helper; it is imported lazily so this
 * module has no build-time dependency on it outside production.
 */
export function registerServiceWorker({
    nav = typeof navigator !== 'undefined' ? navigator : undefined,
    prod = import.meta.env.PROD,
    load = () => import('virtual:pwa-register'),
} = {}) {
    if (!prod || !nav || !('serviceWorker' in nav)) return false
    Promise.resolve()
        .then(load)
        // `immediate` registers on page load rather than waiting for the
        // window `load` event; autoUpdate (see vite.config.js) then swaps in
        // a new worker as soon as one is deployed.
        .then(({ registerSW }) => registerSW({ immediate: true }))
        .catch(() => { /* a broken worker must never break the app */ })
    return true
}
