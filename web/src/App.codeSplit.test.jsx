import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'
import { MemoryRouter } from 'react-router-dom'
import { AppShell } from './App'
import { ThemeProvider } from './ThemeContext'
import { AudioPlayerProvider } from './contexts/AudioPlayerContext'
import { AuthProvider } from './contexts/AuthContext'

/**
 * Route-level code splitting (issue #281).
 *
 * The PWA precaches every built `.js` file on install and re-downloads it on
 * every deploy (`docs/web-pwa.md`, `workbox.globPatterns`). With no dynamic
 * imports the whole app is a single entry chunk, so someone who only opens Home
 * pays for the reader (epub.js), react-markdown and the admin console on their
 * phone, every deploy.
 *
 * Two halves are pinned here:
 *
 *  1. **Source shape.** The heavy, rarely-first-visited routes must reach the
 *     bundler through `lazy(() => import(...))` — that dynamic `import()` is
 *     literally what makes Rollup emit a separate chunk, so a well-meaning
 *     "tidy the imports" pass that hoists one back to the top turns the split
 *     off with nothing else to notice. First-paint pages stay static on purpose.
 *
 *  2. **Runtime.** A lazy route renders the Suspense fallback on its first
 *     synchronous paint and the page only after the chunk resolves. A static
 *     import renders the page immediately, so this fails loudly if the split
 *     is undone — and it also proves the shell has a `<Suspense>` boundary,
 *     without which React throws instead of rendering.
 *
 * Also pinned: the two dead page modules removed with this change
 * (`TranscriptionQueuePage`, and the unreachable default export of
 * `UserManagementPage`) do not come back — both were bundled into that single
 * chunk while being unreachable from any route.
 */

const APP_SRC = fs.readFileSync(path.join(process.cwd(), 'src', 'App.jsx'), 'utf8')

/** Routes that must be split out of the entry chunk. */
const LAZY_PAGES = [
    'BookDetailPage',       // pulls EbookReader -> epub.js, plus react-markdown
    'SystemPage',
    'TranscriptionPage',
    'TranscriptionEditorPage',
    'ImportSourcesPage',
    'TroubleshootPage',
]

/** Routes that must stay in the entry chunk — they are the first paint. */
const EAGER_PAGES = ['HomePage', 'LoginPage', 'LibraryPage']

describe('App.jsx — code-split route table (issue #281)', () => {
    for (const page of LAZY_PAGES) {
        it(`loads ${page} through a dynamic import`, () => {
            expect(APP_SRC).toMatch(
                new RegExp(`const ${page} = lazy\\(\\(\\) => import\\('\\./pages/${page}'\\)\\)`),
            )
            expect(APP_SRC).not.toMatch(
                new RegExp(`^import ${page} from '\\./pages/${page}'`, 'm'),
            )
        })
    }

    for (const page of EAGER_PAGES) {
        it(`keeps ${page} eager, so the first paint costs no extra request`, () => {
            expect(APP_SRC).toMatch(
                new RegExp(`^import ${page} from '\\./pages/${page}'`, 'm'),
            )
        })
    }

    it('wraps the route table in a Suspense boundary', () => {
        expect(APP_SRC).toMatch(/<Suspense[\s\S]*<Routes>[\s\S]*<\/Routes>[\s\S]*<\/Suspense>/)
    })
})

// ---------------------------------------------------------------------------
// Dead page modules (issue #281)
//
// `UserManagementPage`'s default export was imported by App.jsx and never
// rendered — /admin/users is a <Navigate to="/system">, and SystemPage embeds
// the *named* UserManagementSection. `TranscriptionQueuePage` was imported and
// never routed: /transcription/queue renders <TranscriptionPage tab="queue" />,
// and the orphan copy had already drifted (stale since 3c3d2c4, and it grew a
// duplicate Retry column from #247 that nobody could ever see).
//
// Both were bundled anyway. A static import is the only thing that resurrects
// them, so scanning the source is the check.
// ---------------------------------------------------------------------------

/** Source files only — this file names the dead pages in its own comments. */
function sourceFiles(dir, acc = []) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) sourceFiles(full, acc)
        else if (/\.(js|jsx)$/.test(entry.name) && !/\.test\.(js|jsx)$/.test(entry.name)) acc.push(full)
    }
    return acc
}

describe('dead page modules stay gone (issue #281)', () => {
    const root = path.join(process.cwd(), 'src')

    it('has no TranscriptionQueuePage module or reference', () => {
        expect(fs.existsSync(path.join(root, 'pages', 'TranscriptionQueuePage.jsx'))).toBe(false)

        const files = sourceFiles(root)
        expect(files.length).toBeGreaterThan(20)
        const offenders = files.filter(f => fs.readFileSync(f, 'utf8').includes('TranscriptionQueuePage'))
        expect(
            offenders.map(f => path.relative(root, f)),
            '/transcription/queue is served by TranscriptionPage tab="queue"',
        ).toEqual([])
    })

    it('exposes UserManagementPage only as the embedded section', () => {
        const src = fs.readFileSync(path.join(root, 'pages', 'UserManagementPage.jsx'), 'utf8')
        expect(src).toMatch(/export function UserManagementSection/)
        expect(src).not.toMatch(/export default/)
    })
})

// ---------------------------------------------------------------------------
// Runtime: the split actually holds inside the shell.
// ---------------------------------------------------------------------------

vi.mock('./pages/HomePage', () => ({ default: () => <div>home-stub</div> }))
vi.mock('./pages/SystemPage', () => ({ default: () => <div>system-stub</div> }))
vi.mock('./pages/TroubleshootPage', () => ({ default: () => <div>troubleshoot-stub</div> }))
vi.mock('./pages/ImportSourcesPage', () => ({ default: () => <div>import-sources-stub</div> }))
vi.mock('./pages/TranscriptionPage', () => ({ default: () => <div>transcription-stub</div> }))
vi.mock('./pages/TranscriptionEditorPage', () => ({ default: () => <div>editor-stub</div> }))
vi.mock('./pages/BookDetailPage', () => ({ default: () => <div>book-detail-stub</div> }))

vi.mock('./api', async (importOriginal) => ({
    ...await importOriginal(),
    logout: vi.fn().mockResolvedValue(undefined),
    // The pending-registration badge (#282) reads the admin user list on mount.
    getUsers: vi.fn().mockResolvedValue([]),
}))

function renderShell(entry, role = 'admin') {
    render(
        <MemoryRouter initialEntries={[entry]}>
            <ThemeProvider>
                <AuthProvider user={{ username: 'alice', role }}>
                    <AudioPlayerProvider>
                        <AppShell user={{ username: 'alice', role }} setUser={vi.fn()} />
                    </AudioPlayerProvider>
                </AuthProvider>
            </ThemeProvider>
        </MemoryRouter>,
    )
}

describe('AppShell — lazy routes resolve behind the fallback (issue #281)', () => {
    const cases = [
        ['/system/status', 'system-stub'],
        ['/system/troubleshoot', 'troubleshoot-stub'],
        ['/system/import-sources', 'import-sources-stub'],
        ['/transcription', 'transcription-stub'],
        ['/transcription/edit/12', 'editor-stub'],
        ['/book/ebook/34', 'book-detail-stub'],
    ]

    for (const [entry, stub] of cases) {
        it(`shows the fallback, then ${entry}`, async () => {
            renderShell(entry)

            // Synchronous first paint: the chunk has not resolved yet. A static
            // import would have rendered the page here instead.
            expect(screen.getByTestId('route-loading')).toBeInTheDocument()
            expect(screen.queryByText(stub)).toBeNull()

            expect(await screen.findByText(stub)).toBeInTheDocument()
            expect(screen.queryByTestId('route-loading')).toBeNull()
        })
    }

    it('renders an eager route with no fallback at all', () => {
        renderShell('/continue')

        expect(screen.getByText('home-stub')).toBeInTheDocument()
        expect(screen.queryByTestId('route-loading')).toBeNull()
    })
})
