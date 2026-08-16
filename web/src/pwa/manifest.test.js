import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// Issue #62: the web app manifest is a static file in public/. It has no
// runtime code to test, but a broken icon path or a dropped required field
// silently turns "Add to Home Screen" back into a browser shortcut — so pin
// the shape here.
const here = dirname(fileURLToPath(import.meta.url))
const publicDir = resolve(here, '../../public')
const indexHtml = readFileSync(resolve(here, '../../index.html'), 'utf-8')
const manifest = JSON.parse(readFileSync(resolve(publicDir, 'manifest.webmanifest'), 'utf-8'))

describe('web app manifest', () => {
    it('is installable: name, start_url, standalone display, colours', () => {
        expect(manifest.name).toBe('Tandem')
        expect(manifest.short_name).toBe('Tandem')
        expect(manifest.start_url).toBe('/continue')
        expect(manifest.scope).toBe('/')
        expect(manifest.display).toBe('standalone')
        expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i)
        expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i)
    })

    it('ships 192 + 512 any icons and a 512 maskable icon, and every file exists', () => {
        const sizes = manifest.icons.map((i) => `${i.sizes}${i.purpose === 'maskable' ? ':maskable' : ''}`)
        expect(sizes).toEqual(expect.arrayContaining(['192x192', '512x512', '512x512:maskable']))
        for (const icon of manifest.icons) {
            expect(icon.type).toBe('image/png')
            expect(existsSync(resolve(publicDir, icon.src.replace(/^\//, ''))), icon.src).toBe(true)
        }
    })

    it('is linked from index.html together with the iOS touch icon and theme-color', () => {
        expect(indexHtml).toMatch(/<link rel="manifest" href="\/manifest\.webmanifest">/)
        expect(indexHtml).toMatch(/<link rel="apple-touch-icon" href="\/apple-touch-icon-180x180\.png">/)
        expect(indexHtml).toMatch(/<meta name="theme-color" content="#[0-9a-f]{6}">/i)
        expect(indexHtml).toMatch(/<meta name="apple-mobile-web-app-capable" content="yes">/)
        expect(existsSync(resolve(publicDir, 'apple-touch-icon-180x180.png'))).toBe(true)
    })
})
