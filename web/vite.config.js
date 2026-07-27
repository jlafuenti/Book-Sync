/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
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
