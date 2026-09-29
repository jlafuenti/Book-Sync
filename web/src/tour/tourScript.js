/**
 * The web walkthrough script (issue #598, Track A).
 *
 * `TourScript.kt`'s doc comment is the single source of truth for this copy:
 * plain, second person, two or three sentences, and the only jargon is "sync
 * map" — defined the first time it's said. Steps whose title/body are lifted
 * verbatim from the Android script are called out below; `tourScript.test.js`
 * reads `TourScript.kt` at test time and pins that they still match.
 *
 * `reader_progress`'s body is the one deliberate exception: everything up to
 * the last sentence is Android's body verbatim, but the last sentence is
 * swapped for the web-specific setting location (there is no "Display
 * settings" screen on the web; the same choice lives on the reader's
 * Ebook pages / Print pages menu).
 *
 * `done`'s body optionally grows a second sentence — the "put back" promise
 * from the welcome card — when the tour is about to clean up the pair it
 * opened (`cleanUpSuffix`, appended by TourOverlay only when
 * `state.willCleanUp` is true).
 */
import { roleMeets } from '../roles'
import { TourAnchors, TourScreens, TourEvents } from './anchors'

const ADVANCE = {
    next: () => ({ kind: 'next' }),
    tapAnchor: (event) => ({ kind: 'tapAnchor', event }),
    waitFor: (event, skippable = false) => ({ kind: 'waitFor', event, skippable }),
    finish: () => ({ kind: 'finish' }),
}

export const TOUR = [
    {
        id: 'welcome',
        screen: TourScreens.Home,
        anchor: null,
        title: 'Welcome to Tandem',
        body: 'This is a five-minute walkthrough of the app, using your own library. '
            + 'Quit any time with the × in the corner. The book we open along the way is put '
            + 'back the way it was when you finish.',
        advance: ADVANCE.next(),
        // A replay starts from the Account page; the next four steps live
        // on Home, so the tour goes there first.
        goTo: '/continue',
    },
    {
        id: 'home_continue_reading',
        screen: TourScreens.Home,
        anchor: TourAnchors.HomeContinueReading,
        title: 'Continue Reading',
        body: 'Books you have open, in whichever format — ebook or audiobook — you last used. '
            + 'Tapping one picks up exactly where you left off.',
        emptyBody: 'Nothing here yet — Continue Reading appears at the top of Home once you '
            + 'have opened a book. Right after signing in, your places from other devices can '
            + 'take a minute to arrive.',
        advance: ADVANCE.next(),
    },
    {
        id: 'home_next_up',
        screen: TourScreens.Home,
        anchor: TourAnchors.HomeNextUp,
        title: 'Next up',
        body: 'The book you would naturally open next: the newest position you have, in either format.',
        advance: ADVANCE.next(),
    },
    {
        id: 'home_recently_added',
        screen: TourScreens.Home,
        anchor: TourAnchors.HomeRecentlyAdded,
        title: 'Recently Added',
        body: 'New arrivals in your library, newest first.',
        advance: ADVANCE.next(),
    },
    {
        id: 'home_click_library',
        screen: TourScreens.Home,
        anchor: TourAnchors.NavLibrary,
        title: 'Now the Library',
        body: 'Click Library in the sidebar.',
        advance: ADVANCE.tapAnchor(TourEvents.routeShown('/library')),
    },
    {
        id: 'library_filters',
        screen: TourScreens.Library,
        anchor: TourAnchors.LibraryFilterPills,
        title: 'Filters',
        body: 'These pills narrow the list, and Transcribed only hides anything without a '
            + 'finished sync map yet.',
        advance: ADVANCE.next(),
    },
    {
        id: 'library_sort_and_search',
        screen: TourScreens.Library,
        anchor: TourAnchors.LibrarySortSearch,
        title: 'Group and search',
        body: 'Series keeps a series together; search and sort are up here too.',
        advance: ADVANCE.next(),
    },
    {
        id: 'library_upload_and_maintenance',
        screen: TourScreens.Library,
        anchor: TourAnchors.LibraryMaintenance,
        title: 'Upload, Maintenance, Select',
        body: 'Upload adds a file. Maintenance holds Scan Directories, which imports whatever '
            + 'you copied into the library folders, and the pairing tools. Select is for bulk actions.',
        advance: ADVANCE.next(),
        minRole: 'editor',
    },
    {
        id: 'library_open_book',
        screen: TourScreens.Library,
        anchor: TourAnchors.LibraryPairCard,
        title: 'Open a book',
        body: 'Click this book to open its page.',
        advance: ADVANCE.tapAnchor(TourEvents.detailsOpened()),
        needsPair: true,
    },
    {
        id: 'details_pairing',
        screen: TourScreens.Details,
        anchor: TourAnchors.DetailsPairedCard,
        title: 'Paired with',
        body: 'The ebook and its audiobook, and whether they are synced: synced means the sync '
            + 'map exists, the sentence-by-sentence link that lets your place carry over when you '
            + 'switch formats.',
        advance: ADVANCE.next(),
        needsPair: true,
    },
    {
        id: 'details_click_read',
        screen: TourScreens.Details,
        anchor: TourAnchors.DetailsPrimaryAction,
        title: 'Open the reader',
        body: 'Click Read to open the ebook.',
        advance: ADVANCE.tapAnchor(TourEvents.readerOpened()),
        needsPair: true,
    },
    {
        id: 'reader_toolbar',
        screen: TourScreens.Reader,
        anchor: TourAnchors.ReaderToolbar,
        title: 'The reader',
        body: 'Contents, theme and font size live in the toolbar, with your place in the book '
            + 'and the Listen button.',
        advance: ADVANCE.waitFor(TourEvents.readerReady(), false),
        needsPair: true,
    },
    {
        id: 'reader_progress',
        screen: TourScreens.Reader,
        anchor: TourAnchors.ReaderProgress,
        title: 'Your place in the book',
        body: 'Tap the progress text to switch what it shows: percent, page in the book, '
            + 'page in the chapter, or time left in the chapter. Whether pages count the ebook '
            + 'or the print edition is set with the Ebook pages / Print pages menu.',
        advance: ADVANCE.tapAnchor(TourEvents.readerProgressModeChanged()),
        needsPair: true,
    },
    {
        id: 'reader_switch_to_audio',
        screen: TourScreens.Reader,
        anchor: TourAnchors.ReaderSwitchToAudio,
        title: 'Switch to Audio',
        body: 'Click Listen. It jumps to roughly this page in the audiobook.',
        advance: ADVANCE.tapAnchor(TourEvents.playerOpened()),
        needsPair: true,
    },
    {
        id: 'player_paused',
        screen: TourScreens.Player,
        anchor: TourAnchors.PlayerTransport,
        title: 'The audiobook, paused',
        body: 'Opened paused, at that sentence. The transport row below works like any player.',
        advance: ADVANCE.waitFor(TourEvents.playerReady(), false),
        needsPair: true,
    },
    {
        id: 'player_switch_to_reader',
        screen: TourScreens.Player,
        anchor: TourAnchors.PlayerSwitchToReader,
        title: 'Switch to Reader',
        body: 'Click Read to hop back to the page.',
        advance: ADVANCE.tapAnchor(TourEvents.readerOpened()),
        needsPair: true,
    },
    {
        id: 'reader_trick',
        screen: TourScreens.Reader,
        anchor: TourAnchors.ReaderPage,
        title: 'Back in the reader',
        body: 'And it opened the page that goes with the audio. That\'s the whole trick.',
        advance: ADVANCE.next(),
        needsPair: true,
    },
    {
        id: 'click_series',
        screen: TourScreens.Details,
        anchor: TourAnchors.NavSeries,
        title: 'Now Series',
        body: 'Click Series.',
        advance: ADVANCE.tapAnchor(TourEvents.routeShown('/series')),
    },
    {
        id: 'series_groups',
        screen: TourScreens.Series,
        anchor: TourAnchors.SeriesGrid,
        title: 'Series',
        body: 'A series card keeps its books together, in order.',
        emptyBody: 'No series yet: books group here once their metadata carries a series name.',
        advance: ADVANCE.next(),
    },
    {
        id: 'click_transcription',
        screen: TourScreens.Series,
        anchor: TourAnchors.NavTranscription,
        title: 'Now Transcription',
        body: 'Click Transcription.',
        advance: ADVANCE.tapAnchor(TourEvents.routeShown('/transcription')),
    },
    {
        id: 'transcription_status',
        screen: TourScreens.Transcription,
        anchor: TourAnchors.TranscriptionTabs,
        title: 'Transcription',
        body: 'Every pair passes through here: not transcribed, queued, in progress, transcribed. '
            + 'A book only carries your reading position between formats once its transcription finishes.',
        advance: ADVANCE.next(),
    },
    {
        id: 'transcription_queue',
        screen: TourScreens.Transcription,
        anchor: TourAnchors.TranscriptionQueueAll,
        title: 'Queue',
        body: 'Queue sends a pair to the transcription worker; one job runs at a time. Cancel '
            + 'stops a running job at its next checkpoint.',
        advance: ADVANCE.next(),
        minRole: 'admin',
    },
    {
        id: 'troubleshoot_page',
        screen: TourScreens.Troubleshoot,
        anchor: TourAnchors.TroubleshootHeader,
        title: 'Troubleshoot Library',
        body: 'What Tandem found wrong with files, each row with a fix. Run Verification Scan '
            + 'deep-checks audio and ebooks.',
        advance: ADVANCE.next(),
        minRole: 'editor',
        maxRole: 'editor',
        goTo: '/system/troubleshoot',
    },
    {
        id: 'click_system',
        screen: TourScreens.Transcription,
        anchor: TourAnchors.NavSystem,
        title: 'Now System',
        body: 'Click System.',
        advance: ADVANCE.tapAnchor(TourEvents.routeShown('/system')),
        minRole: 'admin',
    },
    {
        id: 'system_status',
        screen: TourScreens.System,
        anchor: TourAnchors.SystemStatus,
        title: 'System status',
        body: 'Books, pairs, the queue and disk at a glance. The update check lives under Updates.',
        advance: ADVANCE.next(),
        minRole: 'admin',
    },
    {
        id: 'system_troubleshoot',
        screen: TourScreens.System,
        anchor: TourAnchors.SystemTroubleshootCard,
        title: 'Troubleshoot Library',
        body: 'Where problems with files show up, each with a fix. Run Verification Scan '
            + 'deep-checks audio and ebooks.',
        advance: ADVANCE.next(),
        minRole: 'admin',
    },
    {
        id: 'system_transcription_settings',
        screen: TourScreens.System,
        anchor: TourAnchors.SystemTranscriptionSettings,
        title: 'Transcription settings',
        body: 'The worker’s address and key, the model, and the off-hours window.',
        advance: ADVANCE.next(),
        minRole: 'admin',
    },
    {
        id: 'system_users',
        screen: TourScreens.System,
        anchor: TourAnchors.SystemUserManagement,
        title: 'Users and invites',
        body: 'Accounts, roles and invites. Registration is invite-only by default.',
        advance: ADVANCE.next(),
        minRole: 'admin',
    },
    {
        id: 'system_backups',
        screen: TourScreens.System,
        anchor: TourAnchors.SystemBackups,
        title: 'Backups',
        body: 'Nightly backups, and restore.',
        advance: ADVANCE.next(),
        minRole: 'admin',
    },
    {
        id: 'click_account',
        // Overridden by stepsForRole() to the screen of whichever step
        // actually precedes it once role filtering is applied — System for
        // an admin, Troubleshoot for an editor, Transcription for a plain
        // user. This placeholder is never rendered.
        screen: TourScreens.Transcription,
        anchor: TourAnchors.NavAccount,
        title: 'Now Account',
        body: 'Click Account.',
        advance: ADVANCE.tapAnchor(TourEvents.routeShown('/account')),
    },
    {
        id: 'account_replay',
        screen: TourScreens.Account,
        anchor: TourAnchors.AccountReplayTour,
        title: 'Replay the walkthrough',
        body: 'Come back here any time you want a refresher.',
        advance: ADVANCE.next(),
    },
    {
        id: 'done',
        screen: TourScreens.Account,
        anchor: null,
        title: 'Done',
        body: 'That\'s Tandem. Enjoy your books.',
        cleanUpSuffix: ' The book we open along the way is put back the way it was when you finish.',
        advance: ADVANCE.finish(),
    },
]

/**
 * The steps a given role sees: drops anything above its `minRole` or below a
 * `maxRole` (troubleshoot_page is editor-exact — admins get the System block
 * instead), then patches `click_account`'s screen to whatever step actually
 * precedes it in that filtered list.
 */
export function stepsForRole(role) {
    const filtered = TOUR.filter((step) => {
        if (step.minRole && !roleMeets(role, step.minRole)) return false
        if (step.maxRole && !roleMeets(step.maxRole, role)) return false
        return true
    })
    const clickAccountIndex = filtered.findIndex((step) => step.id === 'click_account')
    if (clickAccountIndex > 0) {
        filtered[clickAccountIndex] = {
            ...filtered[clickAccountIndex],
            screen: filtered[clickAccountIndex - 1].screen,
        }
    }
    return filtered
}
