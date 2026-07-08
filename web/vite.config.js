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
            // No global floor yet: the app is almost entirely untested, so a global
            // number would be meaningless. The web gate is PATCH coverage (diff-cover
            // in CI): new/changed lines must be >=80% covered. Add a global floor once
            // overall coverage is non-trivial (see docs/testing.md).
        },
    },
})
