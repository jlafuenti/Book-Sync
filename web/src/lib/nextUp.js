import { parseServerDate } from './datetime'

/** How long a series stays in Next up after its last activity (owner's call, #716). */
export const NEXT_UP_WINDOW_DAYS = 90

const DAY_MS = 24 * 60 * 60 * 1000
const KIND_ORDER = { pair: 0, ebook: 1, audiobook: 2 }

const normalizeSeries = (name) => (name || '').trim().toLowerCase()

function tieRank(key) {
    const [kind, id] = key.split('_')
    return [KIND_ORDER[kind] ?? 3, Number(id) || 0]
}

/**
 * "Next up" (issue #716): for each series you are reading, the next book in it.
 *
 * One rule on the web and in the Android app (`NextUp.kt`); both are held to
 * `server/tests/fixtures/sync_parity/next_up_cases.json`.
 *
 * - A **book** is one openable thing: a pair counts once (`pair_N`), an unpaired
 *   ebook or audiobook is its own (`ebook_N` / `audiobook_N`). The caller builds
 *   these; this function never sees the two halves of a pair.
 * - Books group by series name, ignoring case and outer spaces.
 * - A series is **active** when any of its books was started or finished within
 *   [NEXT_UP_WINDOW_DAYS] of `now`. Measured on a real library, an untimed rule
 *   listed 26 series for one reader, most of them books opened long ago.
 * - The **next** book is the lowest-numbered one above the furthest-numbered book
 *   you have touched, and not touched itself. Fractional numbers (novellas such
 *   as 3.5) sit in series order and count. A number the library lacks is skipped
 *   to the next book it has. Ties at one number prefer a pair, then an ebook.
 * - Rows come out most recently active first, like Continue Reading.
 *
 * @param {Array<{key: string, series: ?string, index: ?number}>} books
 *   every book in the library; extra fields ride along to `row.book`.
 * @param {Array<{key: string, completed: boolean, at: string}>} activity
 *   one entry per progress record, keyed like `books`; `at` may be naive UTC.
 * @param {Date} now
 * @returns {Array<{series: string, key: string, book: object, lastActivity: number}>}
 */
export function computeNextUp(books, activity, now) {
    const lastByKey = new Map()
    for (const a of activity) {
        const at = parseServerDate(a.at)?.getTime()
        if (at == null || Number.isNaN(at)) continue
        lastByKey.set(a.key, Math.max(lastByKey.get(a.key) ?? -Infinity, at))
    }

    const groups = new Map()
    for (const book of books) {
        const s = normalizeSeries(book.series)
        if (!s) continue
        if (!groups.has(s)) groups.set(s, [])
        groups.get(s).push(book)
    }

    const cutoff = now.getTime() - NEXT_UP_WINDOW_DAYS * DAY_MS
    const rows = []
    for (const members of groups.values()) {
        const touched = members.filter(b => lastByKey.has(b.key))
        if (touched.length === 0) continue

        const lastActivity = Math.max(...touched.map(b => lastByKey.get(b.key)))
        if (lastActivity < cutoff) continue

        const placed = touched.filter(b => b.index != null)
        if (placed.length === 0) continue
        const frontier = Math.max(...placed.map(b => b.index))

        const candidates = members
            .filter(b => b.index != null && b.index > frontier && !lastByKey.has(b.key))
            .sort((a, b) => {
                if (a.index !== b.index) return a.index - b.index
                const [ka, ia] = tieRank(a.key)
                const [kb, ib] = tieRank(b.key)
                return ka - kb || ia - ib
            })
        if (candidates.length === 0) continue

        const next = candidates[0]
        rows.push({ series: next.series, key: next.key, book: next, lastActivity })
    }

    return rows.sort((a, b) => b.lastActivity - a.lastActivity)
}

/**
 * The web's raw library, reduced to [computeNextUp]'s inputs.
 *
 * A pair becomes one book, `pair_N`, taking its series and number from the
 * ebook (falling back to the audiobook), and opening on the ebook's page, which
 * shows the pairing. Books in no pair keep their own key.
 *
 * Progress counts as activity only when it is real: a finished book, or one
 * with some position. A book merely opened at 0 % is not "being read". Its time
 * is `captured_at`, falling back to `updated_at`, which also moves on
 * server-side rewrites such as a realign (issue #679). Progress on either half
 * of a pair is filed under the pair.
 */
export function libraryToNextUpInput({ ebooks, audiobooks, pairs, progress }) {
    const ebookToPair = new Map()
    const audiobookToPair = new Map()
    const books = []

    for (const pair of pairs) {
        const eb = pair.ebook
        const ab = pair.audiobook
        if (eb?.id) ebookToPair.set(eb.id, pair.id)
        if (ab?.id) audiobookToPair.set(ab.id, pair.id)
        books.push({
            key: `pair_${pair.id}`,
            series: eb?.series || ab?.series || null,
            index: eb?.series_index ?? ab?.series_index ?? null,
            title: eb?.title || ab?.title,
            author: eb?.author || ab?.author,
            coverPath: eb?.cover_path || ab?.cover_path || null,
            detailType: eb?.id ? 'ebook' : 'audiobook',
            detailId: eb?.id ?? ab?.id,
        })
    }
    for (const eb of ebooks) {
        if (ebookToPair.has(eb.id)) continue
        books.push({
            key: `ebook_${eb.id}`, series: eb.series || null, index: eb.series_index ?? null,
            title: eb.title, author: eb.author, coverPath: eb.cover_path || null,
            detailType: 'ebook', detailId: eb.id,
        })
    }
    for (const ab of audiobooks) {
        if (audiobookToPair.has(ab.id)) continue
        books.push({
            key: `audiobook_${ab.id}`, series: ab.series || null, index: ab.series_index ?? null,
            title: ab.title, author: ab.author, coverPath: ab.cover_path || null,
            detailType: 'audiobook', detailId: ab.id,
        })
    }

    const activity = []
    for (const p of progress) {
        const real = p.is_completed
            || (p.media_type === 'ebook' && (p.epub_progress_percent > 0 || p.epub_chapter > 0))
            || (p.media_type === 'audiobook' && p.audio_position_ms > 0)
        if (!real) continue
        let key = null
        if (p.book_pair_id) key = `pair_${p.book_pair_id}`
        else if (p.media_type === 'ebook' && p.ebook_id) {
            key = ebookToPair.has(p.ebook_id) ? `pair_${ebookToPair.get(p.ebook_id)}` : `ebook_${p.ebook_id}`
        } else if (p.media_type === 'audiobook' && p.audiobook_id) {
            key = audiobookToPair.has(p.audiobook_id) ? `pair_${audiobookToPair.get(p.audiobook_id)}` : `audiobook_${p.audiobook_id}`
        }
        if (!key) continue
        activity.push({ key, completed: !!p.is_completed, at: p.captured_at || p.updated_at })
    }

    return { books, activity }
}
