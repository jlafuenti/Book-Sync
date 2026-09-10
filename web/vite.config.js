/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { manualChunks } from './src/build/manualChunks'

export default defineConfig({
    plugins: [
        react(),
        // PWA service worker (issue #62) — see docs/web-pwa.md.
        //
        // Precache the built app shell only. /api/* is bearer-token JSON or
        // short-lived-token media (issue #50): never cached, never used as a
        // navigation fallback. Audio/ebook/cover responses are deliberately
        // NOT cached either — offline media is a separate future feature.
        VitePWA({
            registerType: 'autoUpdate',
            // We ship a hand-written public/manifest.webmanifest and register
            // from src/pwa/registerSw.js (production-only), so the plugin
            // neither generates a manifest nor injects a register script.
            manifest: false,
            injectRegister: null,
            includeAssets: [
                'favicon.ico', 'icon.svg', 'favicon-*.svg',
                'apple-touch-icon-180x180.png', 'pwa-*.png', 'maskable-icon-512x512.png',
            ],
            workbox: {
                globPatterns: ['**/*.{js,css,html,svg,png,ico,woff,woff2}'],
                navigateFallback: '/index.html',
                navigateFallbackDenylist: [/^\/api\//],
                cleanupOutdatedCaches: true,
                clientsClaim: true,
                skipWaiting: true,
                // No runtimeCaching entries on purpose: anything not
                // precached (i.e. every /api/ request) goes to the network.
                runtimeCaching: [],
            },
            // Never in dev: HMR and a worker fight, and tests must stay SW-free.
            devOptions: { enabled: false },
        }),
    ],
    // Route-level splitting lives in App.jsx (issue #281); this only pulls the
    // epub.js dependency tree out of the entry chunk so a deploy that does not
    // touch it leaves its precache entry alone. See src/build/manualChunks.js.
    build: {
        rollupOptions: {
            output: { manualChunks },
        },
    },
    server: {
        port: 3000,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
            },
        },
    },
    test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: './src/test/setup.js',
        coverage: {
            provider: 'v8',
            reporter: ['text', 'cobertura'],
            include: ['src/**/*.{js,jsx}'],
            exclude: ['src/main.jsx', 'src/test/**', '**/*.test.{js,jsx}'],
            // Global floor (anti-backslide), mirroring the server policy in
            // docs/testing.md: floor = total - 3. Raise these after any PR that
            // increases the total. The other web gate is PATCH coverage
            // (diff-cover >=80% on changed lines, PRs only, in web-tests.yml).
            //
            // These sat at 28/28/28/64 long after the suite had grown past them
            // — the doc called them "the furthest behind" and quoted a measured
            // 66/75/48/66 from 2026-09-04. Ratcheted on 2026-09-05 (issue #389)
            // against a measured 78.97 stmts / 78.16 branches / 56.28 funcs /
            // 78.97 lines. Branches is the tightest of the four; the rest carry
            // more slack, which is deliberate — the point of a floor is to
            // catch a backslide, not to fail on noise.
            //
            // Branches was lowered to 58 on 2026-09-09 (PR #447): the vitest 5 /
            // @vitest/coverage-v8 5 bump changed how branches are counted, and
            // the same suite that measured 78.16% under v4 measures 61.82%
            // under v5 (69.39 stmts / 61.82 branches / 60.96 funcs / 73.01
            // lines). No test was lost; the metric moved. 58 is 61.82 − 3,
            // per the ratchet rule in docs/testing.md.
            //
            // NOTE: thresholds apply to whatever ran, so a filtered run
            // (`npx vitest run one.test.jsx --coverage`) will fail them spuriously.
            // Only the full `npm run coverage` is the gate.
            thresholds: {
                lines: 65,
                statements: 65,
                functions: 50,
                branches: 58,
            },
        },
    },
})
