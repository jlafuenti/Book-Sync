import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { TOUR, stepsForRole } from './tourScript'
import { TourEvents } from './anchors'

const HERE = dirname(fileURLToPath(import.meta.url))
const ANDROID_TOUR_SCRIPT = resolve(HERE, '../../../android/app/src/main/java/com/booksync/ui/tour/TourScript.kt')

describe('TOUR', () => {
    it('has unique ids', () => {
        const ids = TOUR.map((s) => s.id)
        expect(new Set(ids).size).toBe(ids.length)
    })

    it('keeps the reader/player loop and closing steps in the documented order', () => {
        const idx = (id) => TOUR.findIndex((s) => s.id === id)
        expect(idx('welcome')).toBe(0)
        expect(idx('home_click_library')).toBeLessThan(idx('library_filters'))
        expect(idx('library_open_book')).toBeLessThan(idx('details_pairing'))
        expect(idx('details_click_read')).toBeLessThan(idx('reader_toolbar'))
        expect(idx('reader_switch_to_audio')).toBeLessThan(idx('player_paused'))
        expect(idx('player_switch_to_reader')).toBeLessThan(idx('reader_trick'))
        expect(idx('reader_trick')).toBeLessThan(idx('click_series'))
        expect(idx('click_series')).toBeLessThan(idx('series_groups'))
        expect(idx('click_transcription')).toBeLessThan(idx('transcription_status'))
        expect(idx('click_account')).toBeLessThan(idx('account_replay'))
        expect(idx('account_replay')).toBeLessThan(idx('done'))
        expect(idx('done')).toBe(TOUR.length - 1)
    })

    it('every tapAnchor/waitFor advance names a real TourEvents kind', () => {
        const validKinds = new Set([
            TourEvents.routeShown('/x').kind,
            TourEvents.detailsOpened().kind,
            TourEvents.readerOpened().kind,
            TourEvents.readerReady().kind,
            TourEvents.readerProgressModeChanged().kind,
            TourEvents.playerOpened().kind,
            TourEvents.playerReady().kind,
        ])
        for (const step of TOUR) {
            if (step.advance.kind === 'tapAnchor' || step.advance.kind === 'waitFor') {
                expect(validKinds.has(step.advance.event.kind)).toBe(true)
            }
        }
    })

    it('marks every needsPair step and no others', () => {
        const needsPairIds = TOUR.filter((s) => s.needsPair).map((s) => s.id)
        expect(needsPairIds).toEqual([
            'library_open_book',
            'details_pairing',
            'details_click_read',
            'reader_toolbar',
            'reader_progress',
            'reader_switch_to_audio',
            'player_paused',
            'player_switch_to_reader',
            'reader_trick',
        ])
    })
})

describe('stepsForRole', () => {
    it('gives a plain user the base script only', () => {
        const steps = stepsForRole('user')
        const ids = steps.map((s) => s.id)
        expect(ids).not.toContain('library_upload_and_maintenance')
        expect(ids).not.toContain('transcription_queue')
        expect(ids).not.toContain('troubleshoot_page')
        expect(ids).not.toContain('click_system')
        expect(ids).not.toContain('system_status')
        expect(steps.length).toBe(23)
    })

    it('gives an editor the maintenance and troubleshoot steps, but not the queue or System', () => {
        // Queue All is admin-gated on the Transcription page (canManageQueue),
        // so the step that spotlights it is too; editors only get Cancel.
        const steps = stepsForRole('editor')
        const ids = steps.map((s) => s.id)
        expect(ids).toContain('library_upload_and_maintenance')
        expect(ids).not.toContain('transcription_queue')
        expect(ids).toContain('troubleshoot_page')
        expect(ids).not.toContain('click_system')
        expect(ids).not.toContain('system_status')
        expect(steps.length).toBe(25)
    })

    it('gives an admin the System block instead of troubleshoot_page', () => {
        const steps = stepsForRole('admin')
        const ids = steps.map((s) => s.id)
        expect(ids).toContain('library_upload_and_maintenance')
        expect(ids).toContain('transcription_queue')
        expect(ids).not.toContain('troubleshoot_page')
        expect(ids).toContain('click_system')
        expect(ids).toContain('system_status')
        expect(ids).toContain('system_backups')
        expect(steps.length).toBe(31)
    })

    it('gives a superadmin the same steps as an admin', () => {
        expect(stepsForRole('superadmin').map((s) => s.id)).toEqual(stepsForRole('admin').map((s) => s.id))
    })

    it('counts N of M against the filtered list', () => {
        expect(stepsForRole('user').length).toBeLessThan(stepsForRole('editor').length)
        expect(stepsForRole('editor').length).toBeLessThan(stepsForRole('admin').length)
    })

    it('patches click_account\'s screen to whatever step precedes it for that role', () => {
        const forUser = stepsForRole('user')
        const forEditor = stepsForRole('editor')
        const forAdmin = stepsForRole('admin')

        const clickAccountFor = (steps) => steps.find((s) => s.id === 'click_account')
        const before = (steps) => steps[steps.findIndex((s) => s.id === 'click_account') - 1]

        expect(clickAccountFor(forUser).screen).toBe(before(forUser).screen)
        expect(clickAccountFor(forEditor).screen).toBe(before(forEditor).screen)
        expect(clickAccountFor(forAdmin).screen).toBe(before(forAdmin).screen)
        // Concretely: an editor's step right before click_account is
        // troubleshoot_page (Troubleshoot); an admin's is system_backups (System).
        expect(before(forEditor).id).toBe('troubleshoot_page')
        expect(clickAccountFor(forEditor).screen).toBe('Troubleshoot')
        expect(before(forAdmin).id).toBe('system_backups')
        expect(clickAccountFor(forAdmin).screen).toBe('System')
    })
})

describe('copy parity with the Android TourScript.kt', () => {
    const kotlinSource = readFileSync(ANDROID_TOUR_SCRIPT, 'utf-8')

    function findBlock(source, id) {
        const idIdx = source.indexOf(`id = "${id}",`)
        if (idIdx === -1) throw new Error(`Android TourStep "${id}" not found`)
        const startIdx = source.lastIndexOf('TourStep(', idIdx)
        const openIdx = source.indexOf('(', startIdx)
        let depth = 0
        let i = openIdx
        for (; i < source.length; i++) {
            if (source[i] === '(') depth++
            else if (source[i] === ')') {
                depth--
                if (depth === 0) break
            }
        }
        return source.slice(startIdx, i + 1)
    }

    function extractField(block, fieldName, stopFields) {
        const marker = `${fieldName} = `
        const start = block.indexOf(marker)
        if (start === -1) return null
        const rest = block.slice(start + marker.length)
        let end = rest.length
        for (const stopField of stopFields) {
            const idx = rest.search(new RegExp(`\\n\\s*${stopField} = `))
            if (idx !== -1 && idx < end) end = idx
        }
        const closeIdx = rest.search(/\n\s*\),?\s*\n/)
        if (closeIdx !== -1 && closeIdx < end) end = closeIdx
        const segment = rest.slice(0, end)
        const matches = [...segment.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1])
        return matches.join('')
    }

    // The Android script's step ids don't all match the web ones one-to-one
    // (the web script uses its own, screen-agnostic id scheme) — this maps
    // the web id to the Android id whose copy it borrows.
    const ANDROID_ID = { welcome: 'home_welcome' }

    function androidStep(id) {
        const block = findBlock(kotlinSource, ANDROID_ID[id] || id)
        return {
            title: extractField(block, 'title', ['body', 'emptyBody', 'advance', 'needsPair', 'altAnchors']),
            body: extractField(block, 'body', ['emptyBody', 'advance', 'needsPair', 'altAnchors']),
        }
    }

    function webStep(id) {
        const step = TOUR.find((s) => s.id === id)
        if (!step) throw new Error(`web TOUR step "${id}" not found`)
        return step
    }

    it.each([
        'welcome',
        'home_continue_reading',
        'home_recently_added',
        'library_filters',
        'reader_trick',
        'account_replay',
    ])('%s matches the Android title and body verbatim', (id) => {
        const android = androidStep(id)
        const web = webStep(id)
        expect(web.title).toBe(android.title)
        expect(web.body).toBe(android.body)
    })

    it('click_system waits for the route the System link actually opens, /system/status', () => {
        // Seen live on the demo as admin (2026-09-29): the link lands on
        // /system/status, routeShown compares routes exactly, and the tour
        // sat on "Now System" after the click.
        expect(webStep('click_system').advance).toEqual({
            kind: 'tapAnchor', event: { kind: 'routeShown', route: '/system/status' },
        })
    })

    it('transcription_queue has an emptyBody: Queue All only renders when a pair is waiting', () => {
        expect(typeof webStep('transcription_queue').emptyBody).toBe('string')
        expect(webStep('transcription_queue').emptyBody.length).toBeGreaterThan(20)
    })

    it('home_next_up has an emptyBody for a fresh account with no positions', () => {
        expect(typeof webStep('home_next_up').emptyBody).toBe('string')
        expect(webStep('home_next_up').emptyBody.length).toBeGreaterThan(20)
    })

    it('reader_toolbar and player_paused are plain next steps, so their copy can be read', () => {
        // As waitFor steps they advanced the moment the reader/player reported
        // ready, within a second of opening, and nobody saw the card.
        expect(webStep('reader_toolbar').advance).toEqual({ kind: 'next' })
        expect(webStep('player_paused').advance).toEqual({ kind: 'next' })
    })

    it('player_paused says the audiobook started playing: the web reader\u2019s Listen autoplays', () => {
        expect(webStep('player_paused').title).toBe('The audiobook')
        expect(webStep('player_paused').body).toContain('started playing')
    })

    it('home_continue_reading matches the Android emptyBody verbatim', () => {
        const block = findBlock(kotlinSource, 'home_continue_reading')
        const androidEmptyBody = extractField(block, 'emptyBody', ['advance', 'needsPair'])
        expect(webStep('home_continue_reading').emptyBody).toBe(androidEmptyBody)
    })

    it('reader_progress matches the Android title, and the body up to its last sentence', () => {
        const android = androidStep('reader_progress')
        const web = webStep('reader_progress')
        expect(web.title).toBe(android.title)
        const androidPrefix = android.body.slice(0, android.body.lastIndexOf('Whether pages'))
        expect(web.body.startsWith(androidPrefix)).toBe(true)
        expect(web.body).not.toBe(android.body)
        expect(web.body).toContain('Ebook pages / Print pages menu')
    })

    it('done matches the Android title and base body, plus the welcome "put back" sentence as a suffix', () => {
        const android = androidStep('done')
        const androidWelcome = androidStep('welcome')
        const web = webStep('done')
        expect(web.title).toBe(android.title)
        expect(web.body).toBe(android.body)
        const putBackSentence = androidWelcome.body.slice(androidWelcome.body.indexOf('The book we open'))
        expect(web.cleanUpSuffix.trim()).toBe(putBackSentence)
    })
})
