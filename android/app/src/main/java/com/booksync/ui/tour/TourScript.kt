package com.booksync.ui.tour

/**
 * Every real control the walkthrough can spotlight (issue #597). Track A
 * defines the vocabulary; Track B tags the Home/Library/Sheet/Details/
 * Downloaded/Account controls with `Modifier.tourAnchor(...)`, and Track C
 * tags the two reader (View-based) anchors. Until an anchor is tagged, the
 * step that names it simply renders degraded (see [TourController]).
 */
enum class TourAnchor {
    HomeContinueReading, HomeRecentlyAdded, HomeInQueue,
    LibraryFilterPills, LibraryTranscribedOnly, LibraryGroupBySeries, LibrarySortSearch,
    CardOverflow,
    SheetRead, SheetListen, SheetDownloadPair, SheetViewDetails,
    DetailsChips, DetailsSyncMapChip, DetailsPrimaryAction, DetailsRefreshSync, DetailsUnlink,
    ReaderPage, ReaderSwitchToAudio,
    PlayerTransport, PlayerSwitchToReader,
    DownloadedPills, AccountStorage, AccountServer, AccountReplayTour,
}

/** Which screen a [TourStep] belongs to — drives the nav-host's tab/route wiring. */
enum class TourScreen { Home, Library, Sheet, Details, Reader, Player, Downloaded, Account }

/** How the user gets from one [TourStep] to the next. */
sealed class Advance {
    /** The card's own Next button. */
    data object Next : Advance()

    /** The user taps the spotlighted anchor; Next is disabled until [expect] fires. */
    data class TapAnchor(val expect: TourEvent) : Advance()

    /** Waiting on something that happens off a direct tap on the anchor (e.g. a gesture). */
    data class WaitFor(val event: TourEvent, val skippable: Boolean) : Advance()
}

/**
 * Something the app reports back to [TourController] as the user (or the app,
 * on the user's behalf) does the guided thing a step is waiting for.
 *
 * [SheetOpened], [DetailsOpened], [ReaderOpened] and [PlayerOpened] carry the
 * pair id the screen actually opened for, but [TourStep.advance] is built
 * once, statically, before any pair is chosen — so matching an expected event
 * against an incoming one (see [TourEvent.matchesKind]) compares only the
 * event's kind, never the id it carries. [RouteShown] and [AnchorTapped] are
 * the exception: their payload *is* the thing being matched.
 */
sealed class TourEvent {
    data class SheetOpened(val pairId: Int) : TourEvent()
    data class DetailsOpened(val pairId: Int) : TourEvent()
    data class ReaderOpened(val pairId: Int) : TourEvent()
    data object ReaderBarsShown : TourEvent()
    data object ReaderSyncedSelection : TourEvent()
    data class PlayerOpened(val pairId: Int) : TourEvent()
    /** The card sheet went away (dismissed or its screen left) while a Sheet step was current. */
    data object SheetClosed : TourEvent()
    data class RouteShown(val route: String) : TourEvent()
    data class AnchorTapped(val anchor: TourAnchor) : TourEvent()

    /**
     * Whether [actual] is the event this one describes. For the four
     * pair-carrying variants that means "same kind" only — the pair id in a
     * statically-built [TourStep.advance] is a placeholder ([ANY_PAIR]).
     * [RouteShown] and [AnchorTapped] compare their payload too, since that
     * payload is exactly what a step is naming.
     */
    fun matchesKind(actual: TourEvent): Boolean = when (this) {
        is SheetOpened -> actual is SheetOpened
        is DetailsOpened -> actual is DetailsOpened
        is ReaderOpened -> actual is ReaderOpened
        ReaderBarsShown -> actual is ReaderBarsShown
        ReaderSyncedSelection -> actual is ReaderSyncedSelection
        SheetClosed -> actual is SheetClosed
        is PlayerOpened -> actual is PlayerOpened
        is RouteShown -> actual is RouteShown && actual.route == route
        is AnchorTapped -> actual is AnchorTapped && actual.anchor == anchor
    }
}

/** Placeholder pair id used only inside the static [TOUR] script — see [TourEvent.matchesKind]. */
private const val ANY_PAIR = -1

/**
 * One card of the walkthrough.
 *
 * @param emptyBody shown instead of [body] when the step's [anchor] never
 *   shows up (the control isn't on this build/server/role) or, for the
 *   reader selection step, when there is nothing to select against.
 * @param needsPair true for every step that only makes sense once
 *   [TourPairPicker] has chosen a real pair; with no qualifying pair the
 *   whole run of `needsPair` steps collapses to one skip card
 *   (see [TourController]).
 */
data class TourStep(
    val id: String,
    val screen: TourScreen,
    val anchor: TourAnchor?,
    val title: String,
    val body: String,
    val emptyBody: String? = null,
    val advance: Advance = Advance.Next,
    val needsPair: Boolean = false,
)

/** The id of the one step where the tour must not block the reader's own gestures. */
const val READER_SELECTION_STEP_ID = "reader_select_sentence"

/**
 * The walkthrough script (issue #597) — ≈22 real steps plus a closing "Done"
 * card, in the order the owner specced: Home orientation, open a pair from
 * Library, the sheet, the details screen, read a page and sync a sentence to
 * audio, the player (paused), back in the reader, Library's filters, the
 * Downloaded tab, and Account — ending on "Replay the walkthrough" and Done.
 *
 * This copy is the single source of truth for the web tour (#598) too: plain,
 * second person, two or three sentences, and the only piece of jargon it uses
 * is "sync map" — defined the first time it's said.
 */
val TOUR: List<TourStep> = listOf(
    TourStep(
        id = "home_welcome",
        screen = TourScreen.Home,
        anchor = null,
        title = "Welcome to Tandem",
        body = "This is a five-minute walkthrough of the app, using your own library. " +
            "Quit any time with the × in the corner — nothing you do here is saved differently " +
            "from using the app normally.",
    ),
    TourStep(
        id = "home_continue_reading",
        screen = TourScreen.Home,
        anchor = TourAnchor.HomeContinueReading,
        title = "Continue Reading",
        body = "Books you have open, in whichever format — ebook or audiobook — you last used. " +
            "Tapping one picks up exactly where you left off.",
    ),
    TourStep(
        id = "home_recently_added",
        screen = TourScreen.Home,
        anchor = TourAnchor.HomeRecentlyAdded,
        title = "Recently Added",
        body = "New arrivals in your library, newest first.",
    ),
    TourStep(
        id = "home_in_queue",
        screen = TourScreen.Home,
        anchor = TourAnchor.HomeInQueue,
        title = "In Queue",
        body = "Books the server is still transcribing. A book only carries your reading " +
            "position between formats once this finishes.",
        emptyBody = "Empty right now — this section fills in whenever a book is queued for transcription.",
    ),
    TourStep(
        id = "library_open_pair",
        screen = TourScreen.Library,
        anchor = TourAnchor.CardOverflow,
        title = "Open a book",
        body = "Tap the three dots on this book to see what you can do with it.",
        advance = Advance.TapAnchor(TourEvent.SheetOpened(ANY_PAIR)),
        needsPair = true,
    ),
    TourStep(
        id = "sheet_stream",
        screen = TourScreen.Sheet,
        anchor = TourAnchor.SheetRead,
        title = "Read or Listen",
        body = "Read opens the ebook, Listen opens the audiobook — both stream from the server; " +
            "nothing is downloaded yet.",
        needsPair = true,
    ),
    TourStep(
        id = "sheet_download",
        screen = TourScreen.Sheet,
        anchor = TourAnchor.SheetDownloadPair,
        title = "Download pair",
        body = "Downloads the ebook, the audiobook and the sync data together, for reading " +
            "offline or casting to a speaker.",
        needsPair = true,
    ),
    TourStep(
        id = "sheet_view_details",
        screen = TourScreen.Sheet,
        anchor = TourAnchor.SheetViewDetails,
        title = "View details",
        body = "Tap View details to see what Tandem knows about this pair.",
        advance = Advance.TapAnchor(TourEvent.DetailsOpened(ANY_PAIR)),
        needsPair = true,
    ),
    TourStep(
        id = "details_chips",
        screen = TourScreen.Details,
        anchor = TourAnchor.DetailsChips,
        title = "Ebook, Audiobook, Sync map",
        body = "Green means that piece is already on this device.",
        needsPair = true,
    ),
    TourStep(
        id = "details_sync_map",
        screen = TourScreen.Details,
        anchor = TourAnchor.DetailsSyncMapChip,
        title = "The sync map",
        body = "The sync map is the sentence-by-sentence link between the ebook and the " +
            "audiobook. Without it, your place can't carry over when you switch formats.",
        needsPair = true,
    ),
    TourStep(
        id = "details_maintenance",
        screen = TourScreen.Details,
        anchor = TourAnchor.DetailsUnlink,
        title = "Refresh sync data, Unlink pair",
        body = "Refresh sync data replaces an old sync map after the book is re-transcribed, " +
            "since its old timestamps no longer line up. Unlink pair undoes an auto-match that " +
            "paired the wrong files — editors only.",
        emptyBody = "Neither row shows here right now: there's nothing to refresh until a sync map is on " +
            "this device, and Unlink pair only appears for editors.",
        needsPair = true,
    ),
    TourStep(
        id = "details_tap_read",
        screen = TourScreen.Details,
        anchor = TourAnchor.DetailsPrimaryAction,
        title = "Open the reader",
        body = "Tap Read to open the ebook.",
        advance = Advance.TapAnchor(TourEvent.ReaderOpened(ANY_PAIR)),
        needsPair = true,
    ),
    TourStep(
        id = "reader_tap_page",
        screen = TourScreen.Reader,
        anchor = TourAnchor.ReaderPage,
        title = "The reader",
        body = "Tap the middle of the page to bring up the toolbar.",
        emptyBody = "The reader's toolbar isn't available to spotlight yet on this build — " +
            "tap the middle of the page to bring it up.",
        advance = Advance.WaitFor(TourEvent.ReaderBarsShown, skippable = false),
        needsPair = true,
    ),
    TourStep(
        id = "reader_switch_to_audio",
        screen = TourScreen.Reader,
        anchor = TourAnchor.ReaderSwitchToAudio,
        title = "Switch to Audio",
        body = "Jumps to roughly this page in the audiobook — quick, but not sentence-precise.",
        needsPair = true,
    ),
    TourStep(
        id = READER_SELECTION_STEP_ID,
        screen = TourScreen.Reader,
        // Reuses ReaderPage: this step spotlights nothing narrower than the
        // page itself, and the overlay leaves the whole page unblocked so the
        // real press-and-drag selection gesture reaches the reader beneath it.
        anchor = TourAnchor.ReaderPage,
        title = "Sync a sentence to audio",
        body = "Press and hold a word, drag to select a sentence, then tap Sync to Audio. " +
            "This is the precise version of the jump you just saw.",
        emptyBody = "You're offline with nothing downloaded for this book, so this step can't " +
            "run here. Skip uses the page-level sync instead.",
        advance = Advance.WaitFor(TourEvent.ReaderSyncedSelection, skippable = true),
        needsPair = true,
    ),
    TourStep(
        id = "player_paused",
        screen = TourScreen.Player,
        anchor = TourAnchor.PlayerTransport,
        title = "The audiobook, paused",
        body = "Opened paused, at that sentence. The transport row below works like any player.",
        needsPair = true,
    ),
    TourStep(
        id = "player_switch_to_reader",
        screen = TourScreen.Player,
        anchor = TourAnchor.PlayerSwitchToReader,
        title = "Switch to Reader",
        body = "Tap Switch to Reader to hop back to the page.",
        advance = Advance.TapAnchor(TourEvent.ReaderOpened(ANY_PAIR)),
        needsPair = true,
    ),
    TourStep(
        id = "reader_trick",
        screen = TourScreen.Reader,
        anchor = TourAnchor.ReaderPage,
        title = "Back in the reader",
        body = "And it opened the page that goes with the audio. That's the whole trick.",
        needsPair = true,
    ),
    TourStep(
        id = "library_filters",
        screen = TourScreen.Library,
        anchor = TourAnchor.LibraryFilterPills,
        title = "Filters",
        body = "These pills narrow the list, and Transcribed only hides anything without a " +
            "finished sync map yet.",
    ),
    TourStep(
        id = "library_sort_and_group",
        screen = TourScreen.Library,
        anchor = TourAnchor.LibrarySortSearch,
        title = "Group and search",
        body = "Group by series keeps a series together; search and sort are up here too.",
    ),
    TourStep(
        id = "downloaded_pills",
        screen = TourScreen.Downloaded,
        anchor = TourAnchor.DownloadedPills,
        title = "Downloaded",
        body = "Everything on this device, for offline reading and listening. The same three-dot " +
            "menu here can delete a download without touching your progress on the server.",
    ),
    TourStep(
        id = "account_storage_and_server",
        screen = TourScreen.Account,
        anchor = TourAnchor.AccountStorage,
        title = "Storage and Server",
        body = "Storage can clean up downloads automatically once you finish a book. Server is " +
            "where you'd point the app at a different Tandem server — changing it signs you out.",
    ),
    TourStep(
        id = "account_replay",
        screen = TourScreen.Account,
        anchor = TourAnchor.AccountReplayTour,
        title = "Replay the walkthrough",
        body = "Come back here any time you want a refresher.",
    ),
    TourStep(
        id = "done",
        screen = TourScreen.Account,
        anchor = null,
        title = "Done",
        body = "That's Tandem. Enjoy your books.",
    ),
)

/**
 * The single skip card shown in place of every `needsPair` step when
 * [TourPairPicker.pick] finds nothing to open (issue #597 §3). Explained,
 * never silently skipped.
 */
val NO_PAIR_SKIP_STEP = TourStep(
    id = "skipped_no_pair",
    screen = TourScreen.Library,
    anchor = null,
    title = "Skipped",
    body = "Skipped: your library has no synced pair yet. The rest of the walkthrough still " +
        "applies once one is ready.",
)
