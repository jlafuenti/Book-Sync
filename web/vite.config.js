/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

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
            // docs/testing.md: floor = total - 3. Totals when this was added:
            // 31 stmts / 67.47 branches / 31.32 funcs / 31 lines. Raise these after
            // any PR that increases the total. The other web gate is PATCH coverage
            // (diff-cover >=80% on changed lines, PRs only, in web-tests.yml).
            // NOTE: thresholds apply to whatever ran, so a filtered run
            // (`npx vitest run one.test.jsx --coverage`) will fail them spuriously.
            // Only the full `npm run coverage` is the gate.
            thresholds: {
                lines: 28,
                statements: 28,
                functions: 28,
                branches: 64,
            },
        },
    },
})
