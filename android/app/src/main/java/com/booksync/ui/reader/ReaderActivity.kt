package com.booksync.ui.reader

import android.content.ComponentName
import android.os.Bundle
import android.os.Handler
import android.os.SystemClock
import android.util.Log
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.webkit.WebView
import android.widget.FrameLayout
import android.widget.SeekBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.doOnNextLayout
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionToken
import com.booksync.R
import com.booksync.data.auth.hasMinRole
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.SyncPointEntity
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.ReaderPositionSnapshot
import com.booksync.data.repository.SyncMapInUse
import com.booksync.data.repository.toStoredPosition
import com.booksync.data.sync.HINT_READIUM_LOCATOR
import com.booksync.data.sync.StoredPosition
import com.booksync.data.sync.planRestore
import com.booksync.data.util.NetworkMonitor
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import com.booksync.player.AudioPlayerService
import com.booksync.player.MediaId
import com.booksync.player.PairMediaItems
import com.booksync.ui.components.TranscriptionStatusDialog
import com.booksync.ui.tour.TourAnchor
import com.booksync.ui.tour.TourAnchorRegistry
import com.booksync.ui.tour.TourController
import com.booksync.ui.tour.TourEvent
import com.booksync.ui.tour.TourNav
import com.booksync.ui.tour.TourScreen
import com.booksync.ui.tour.TourState
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.snackbar.Snackbar
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
import androidx.compose.ui.geometry.Rect as ComposeRect
import org.readium.r2.navigator.Decoration
import org.readium.r2.navigator.epub.EpubNavigatorFactory
import org.readium.r2.navigator.epub.EpubNavigatorFragment
import org.readium.r2.navigator.input.InputListener
import org.readium.r2.navigator.input.TapEvent
import org.readium.r2.navigator.preferences.ReadingProgression
import org.readium.r2.shared.ExperimentalReadiumApi
import org.readium.r2.shared.publication.Locator
import org.readium.r2.shared.publication.Publication
import org.readium.r2.shared.publication.Link
import org.readium.r2.shared.publication.epub.pageList
import org.readium.r2.shared.util.AbsoluteUrl
import org.readium.r2.shared.util.Try
import org.readium.r2.shared.util.Url
import org.readium.r2.shared.util.asset.AssetRetriever
import org.readium.r2.shared.util.data.Container
import org.readium.r2.shared.util.data.ReadError
import org.readium.r2.shared.util.http.DefaultHttpClient
import org.readium.r2.shared.util.resource.Resource
import org.readium.r2.shared.util.resource.TransformingContainer
import org.readium.r2.shared.util.resource.TransformingResource
import org.readium.r2.shared.util.toAbsoluteUrl
import org.readium.r2.streamer.PublicationOpener
import org.readium.r2.streamer.parser.DefaultPublicationParser
import java.io.File
import javax.inject.Inject
import kotlin.coroutines.resume

/**
 * Activity hosting the Readium EpubNavigatorFragment for real EPUB rendering.
 * Features: toggle toolbar on tap, progress slider, TOC, font settings.
 */
@OptIn(ExperimentalReadiumApi::class)
@AndroidEntryPoint
class ReaderActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PAIR_ID = "pairId"

        /**
         * The audiobook's position when the user pressed "Switch to Reader".
         * Present only for that handoff; absent for every ordinary open, which
         * restores from the shared ladder alone. See [withHandoffAnchor].
         */
        const val EXTRA_HANDOFF_AUDIO_MS = "handoffAudioMs"
        /** The player's Read along entry: the reader follows the audio that keeps playing (issue #762). */
        const val EXTRA_READ_ALONG = "readAlong"

        /**
         * A standalone (unpaired) ebook — issue #169. Mutually exclusive with
         * [EXTRA_PAIR_ID]: with this set there is no pair, no sync map and no
         * audio to hand off to, and the position lives under the `ebook` scope.
         */
        const val EXTRA_EBOOK_ID = "ebookId"
        const val RESULT_SWITCH_TO_AUDIO = 42
        private const val TAG = "ReaderActivity"
        private const val NAV_FRAGMENT_TAG = "EpubNavigatorFragment"
        private const val SAVE_INTERVAL_MS = 5000L
        // How long after onConfigurationChanged a locator emission is treated
        // as Readium's re-layout echo rather than a user page turn. The
        // re-layout recomputes pagination and can emit a chapter-top-quantized
        // locator; saving it regressed the anchor (observed live after issue
        // #163 made rotation survivable — the emission landed ~200 ms after
        // the re-layout finished drawing).
        private const val RELAYOUT_ECHO_WINDOW_MS = 2000L
        // How long after a configuration change to wait before re-anchoring
        // the view to the pre-change locator (re-layout finishes drawing in
        // ~200-400 ms; the go() must land after it or Readium re-lays again).
        private const val RELAYOUT_RESTORE_DELAY_MS = 800L
        // How long the page count waits after the first locator, a settings
        // change or a size change before capturing the live page (issue #730):
        // long enough for Readium to have re-laid out, and it folds a burst of
        // changes (stepping the font size) into one count.
        private const val PAGE_COUNT_DEBOUNCE_MS = 750L
        // How long to wait for the hidden counter to take a new size.
        private const val PAGE_COUNTER_LAYOUT_TIMEOUT_MS = 1000L
        // How many times a failed page count is retried for the same layout.
        private const val PAGE_COUNT_MAX_RETRIES = 2
        // Waits before each live page probe attempt (issue #730); a newer locator cancels them.
        private val PROBE_RETRY_DELAYS_MS = longArrayOf(0L, 250L, 500L, 1000L, 2000L, 4000L)
        // Read-along (issue #762): how often the audio position is sampled, and
        // the toolbar icon's alpha while following is off (255 while on). It was
        // 500 ms, the cadence PlayerViewModel and MiniPlayerBar poll at; the word
        // mark (issue #836) needs a finer one, since a spoken word can be 200 ms.
        // Sentence work stays cheap at this rate: the controller yields a
        // Decorate only when the sentence changes, and a Word only when the
        // token does, so an idle tick is one binary search and no WebView call.
        private const val READ_ALONG_POLL_MS = 150L
        private const val READ_ALONG_ICON_OFF_ALPHA = 140
        // How long after a suspect locator emission to wait before asking the
        // page whether the sentence is still on screen (Readium's snap-back
        // and settle emissions arrive well inside this).
        private const val READ_ALONG_SETTLE_MS = 700L
        // Readium decoration group for the sentence being followed; applying an
        // empty list to it removes the mark without touching other decorations.
        private const val READ_ALONG_DECORATION_GROUP = "read-along"
    }

    @Inject lateinit var repository: BookSyncRepository
    @Inject lateinit var dictionaryRepository: com.booksync.data.repository.DictionaryRepository
    @Inject lateinit var tokenManager: TokenManager
    @Inject lateinit var networkMonitor: NetworkMonitor
    /** The configured server, for the stream URL read-along loads when the book is not downloaded (issue #762). */
    @Inject lateinit var serverUrlManager: com.booksync.data.remote.ServerUrlManager
    /** The shareable app log; the progress indicator's page-count drift goes here (issue #730). */
    @Inject lateinit var diagnosticLogger: DiagnosticLogger

    /**
     * The app-scoped walkthrough engine and its anchor registry (issue #597
     * Track C). Both are bound `@Singleton` in the Hilt graph already (see
     * `di/AppModule.kt`'s `provideTourController` and
     * `TourAnchorRegistry`'s own `@Inject constructor`), so — unlike
     * `BookSyncNavigation`, which is Compose and has no other route to the
     * graph — a plain `@Inject lateinit var` on this `@AndroidEntryPoint`
     * Activity reaches the same instances directly, no `EntryPoint` needed.
     */
    @Inject lateinit var tourController: TourController
    @Inject lateinit var tourRegistry: TourAnchorRegistry

    private var publication: Publication? = null

    /**
     * Server chapter numbering for the open book (issue #804): the server and
     * the web count every OPF spine item, Readium's `readingOrder` leaves out
     * `linear="no"` ones. Read from the EPUB once it is open; until then, or
     * for a book that cannot be mapped, the identity. Used only where a chapter
     * crosses to or from something server-shaped — see [serverChapterOf].
     */
    private var chapterMap: SpineChapterMap? = null
    private val chapterNumbering: SpineChapterMap
        get() = chapterMap ?: SpineChapterMap.identity(publication?.readingOrder?.size ?: 0)
    private var navigator: EpubNavigatorFragment? = null
    private var pair: BookPairEntity? = null
    private var pairId: Int = 0

    /**
     * Non-null while [TranscriptionStatusDialog] is up over the "Switch to
     * Audio" toolbar action (issue #536) — set by
     * [checkReadinessThenSyncAudioToPage], read by the Compose content in
     * [R.id.overlay_host].
     */
    private val pendingSwitchStatus = mutableStateOf<TranscriptionStatus?>(null)

    /** Refreshed each time the dialog above is raised — see [checkReadinessThenSyncAudioToPage]. */
    private val canTranscribeSwitch = mutableStateOf(false)

    /**
     * Standalone mode (issue #169): non-zero when opened on an unpaired ebook.
     * [isStandalone] is the single switch every pair-dependent branch reads, so
     * that "does this book have audio" is asked once rather than inferred from
     * a null pair in a dozen places.
     */
    private var ebookId: Int = 0
    private var standaloneEbook: com.booksync.data.local.entity.EBookEntity? = null
    private val isStandalone: Boolean get() = ebookId != 0
    /**
     * See [HandoffAudioAnchor] (issue #682 follow-up). Set from the intent
     * once in [onCreate]; [getInitialLocator] consumes it, so a later call
     * from [reanchorAfterResume] plans only from the freshly fetched record.
     */
    private var handoffAudio = HandoffAudioAnchor(0)
    private var positionSaveJob: Job? = null
    /** Collects [TourController.nav] for the two reader-only requests — see [onCreate]. */
    private var tourNavJob: Job? = null
    private var isBarVisible = false
    private var isSeeking = false
    private val chapterTextCache = mutableMapOf<Int, String?>() // reading-order index → plain text cache
    // Precomputed content-weighted chapter lengths for accurate slider→position mapping
    private var chapterLengthsDeferred: Deferred<LongArray>? = null
    /** When true, savePosition skips overwriting the audio bookmark (preserves sentence sync). */
    private var sentenceSyncPending = false

    /** The canonical record this reader opened with, server-fresh where possible. */
    private var canonicalPosition: StoredPosition? = null

    /**
     * The audio position the reader last knew [canonicalPosition] to hold —
     * [ResumeReanchorPolicy]'s comparison point for whether a resume after
     * backgrounding needs to re-run the restore ladder (issue #682). Set at
     * open and refreshed after every reader save lands in Room (see
     * [savePosition]'s call to [BookSyncRepository.saveReaderPosition]), so a
     * page turned while the reader stayed in the foreground keeps this
     * current without ever touching [reanchorAfterResume]. Null only when
     * nothing has established a baseline yet.
     */
    private var baselineAudioMs: Int? = null

    /**
     * Set in [onStop], consumed by the very next [onStart] (issue #682). A
     * reader that has never been backgrounded has nothing to re-anchor
     * against — the ladder it ran in [onCreate] is still the freshest thing
     * it knows. Only a return FROM the background can mean something was
     * consumed elsewhere while this reader sat unattended.
     */
    private var hasStoppedSinceOpen = false

    /**
     * True while [reanchorAfterResume] is deciding whether to re-anchor and,
     * if so, moving the navigator there. [savePosition] checks this first and
     * drops the call outright — not queuing it — because a save landing
     * mid-decision would write exactly the stale page this whole mechanism
     * exists to correct, from either the collector in [startPositionTracking]
     * or the explicit exit save in [onPause].
     */
    private var savesBlockedForReanchor = false

    /**
     * The debounce window [startPositionTracking]'s collector measures
     * against — promoted out of that coroutine's local scope so
     * [reanchorAfterResume] can reset it after navigating: without this, a
     * throttle window that had already elapsed while saves were blocked
     * could fire an autosave on the very next locator emission, before the
     * settle from the re-anchor's own `navigator.go(...)` is recognizable as
     * an echo.
     */
    private var lastSaveTime = 0L

    /**
     * The total-progression fraction the tour wants this open jumped to, past
     * the cover/copyright/dedication pages — issue #597 follow-up. Decided in
     * [getInitialLocator], the one place that already knows whether the
     * restore ladder used a real saved position, and applied once the
     * navigator exists (see [applyTourReaderJump]). Null on every ordinary,
     * non-tour open, and on a tour open where a real position exists.
     */
    private var tourJumpProgression: Double? = null

    /**
     * Replaces the old `positionEstablished` boolean latch, which had two
     * defects: an unresolved restore left it false forever (suppressing every
     * later save, even after real page-turns), and the catch in
     * `getInitialLocator` could reset it to false after an earlier rung had
     * already landed. See [PositionSavePolicy].
     */
    private val savePolicy = PositionSavePolicy()

    /** Drops the redundant second write every reader exit used to make. */
    private val duplicatePositionFilter = DuplicatePositionFilter()

    /**
     * The escape hatch's decision half (issue #373) — see
     * [ResourceFailurePolicy] and [handleResourceLoadFailed].
     */
    private val resourceFailurePolicy = ResourceFailurePolicy()

    /**
     * The href + progression of the last locator the code displayed
     * programmatically — the initial restored locator, or a `navigator.go(...)`
     * call such as [goToProgress]. Used by `startPositionTracking`'s collector
     * to recognize Readium's "settle" emission (issue #61/#40 fix 1): the
     * `currentLocator` StateFlow emits again right after ANY programmatic
     * display settles in the WebView, with a computed progression but no user
     * input at all. The old code only skipped the very FIRST emission
     * (`awaitingRestoreLocator`, a single-shot flag that was also never reset
     * on tracker re-entry) — the settle emission slipped through as the
     * "first real" one and got misread as [savePolicy]'s
     * `onUserNavigation()`, which could upgrade an unresolved restore to a
     * full save and write spine 0 over a real server position. Comparing
     * against this target on every emission (see [isProgrammaticEcho]) fixes
     * both: it isn't consumed after one use, and re-entering
     * `startPositionTracking` doesn't reset anything back to "everything is
     * an echo".
     */
    private var programmaticTarget: Locator? = null

    // Set by onConfigurationChanged; emissions within RELAYOUT_ECHO_WINDOW_MS
    // of it are re-layout echoes, not navigation (issue #163 companion fix).
    private var lastConfigChangeAtMs = 0L

    // Where the user actually was before the configuration change. Readium's
    // re-layout can settle at the top of the chapter — merely suppressing the
    // save is not enough, because the exit's onPause save persists whatever
    // the view shows. Captured on the FIRST config change of a burst (a
    // rotate-back inside the window must not capture the drifted position),
    // then re-navigated to once the re-layout settles.
    private var relayoutRestoreTarget: Locator? = null
    private var relayoutRestoreJob: kotlinx.coroutines.Job? = null

    // ============ Read-along state (issue #762) ============

    /** Replaced with the pair's sync points each time following starts; see [startFollowing]. */
    private var readAlong: ReadAlongController = ReadAlongController(emptyList())
    private var readAlongMediaController: MediaController? = null
    private var readAlongPollJob: Job? = null
    // Word mark (issue #836): the sentence whose token ranges the page search
    // built, how many it built (0 = not located, or no CSS.highlights), and the
    // token the audio wants marked, remembered so a late locate can draw it.
    private var wordMarkPoint: SyncPointEntity? = null
    private var wordMarkTokenCount = 0
    private var wordMarkWanted = -1
    /** The pending settle-then-probe for a suspect locator emission; see [verifySuspectedTurn]. */
    private var suspectVerifyJob: Job? = null

    /** One-shot: the [EXTRA_READ_ALONG] request is honoured on the first locator only. */
    private var readAlongRequested = false

    // UI views
    private lateinit var topBar: View
    private lateinit var bottomBar: View
    private lateinit var toolbar: MaterialToolbar
    private lateinit var progressText: TextView
    private lateinit var progressSlider: SeekBar
    private lateinit var readAlongBar: View
    private lateinit var readAlongPlay: android.widget.ImageButton
    private lateinit var readAlongTime: TextView
    private lateinit var backToAudio: com.google.android.material.button.MaterialButton

    // ============ Progress indicator state (issue #730) ============

    /** Mode, page mode, speed samples and print page counts, in the shared reader prefs file. */
    private val progressPrefs: ReaderProgressPrefs by lazy {
        ReaderProgressPrefs(getSharedPreferences(ReaderDisplaySettings.PREFS_NAME, MODE_PRIVATE))
    }

    /** Everything the tap-to-cycle indicator shows; see [ReaderProgressState]. */
    private val progressState: ReaderProgressState by lazy { ReaderProgressState(progressPrefs) }

    private val pageCountCache: PageCountCache by lazy { PageCountCache(File(filesDir, "page_counts")) }

    /** The reading order's hrefs, as locator hrefs are compared against them. */
    private var spineHrefs: List<String> = emptyList()

    /** The book's embedded page list, resolved against [spineHrefs] once per open. */
    private var pageListInputs = ReaderProgressInputs.PageList(emptyList(), emptyList())

    /** The EPUB file's size: part of the page count's cache key. */
    private var ebookFileLength = 0L

    /** The hidden WebView that counts pages, created on the first count. */
    private var pageCounter: PageCounterWebView? = null
    private var probeJob: Job? = null
    private var pageCountTriggerJob: Job? = null
    private var pageCountJob: Job? = null
    private var pageCountSettingsJob: Job? = null

    /** The cache key of the counts on show, and of the count running, if any. */
    private var countedKey: String? = null
    private var countingKey: String? = null

    /** Set by the first locator emission; the page count waits for it. */
    private var pageCountStarted = false

    /**
     * Set once a capture got as far as a cache key. Until then each locator
     * retries it (the first page shown may not have been capturable - a
     * resource outside the reading order, a page still loading); after it,
     * only a settings or size change recounts.
     */
    private var pageLayoutCaptured = false

    /** Failed counts per layout key, so a failing layout is retried at most [PAGE_COUNT_MAX_RETRIES] times. */
    private val pageCountFailures = mutableMapOf<String, Int>()

    /** Head captures per resource, capped so one that cannot succeed is not retried every page turn (#736). */
    private val headCaptureRetries = HeadCaptureRetries()

    /** `assets/tandem/live-probe.js`, read once: every page probe sends it (issue #736). */
    private val liveProbeSource: String by lazy {
        assets.open(LivePageProbe.SOURCE_ASSET).bufferedReader().use { it.readText() }
    }

    /** The ebook this reader shows, standalone or paired: the page count and print data are per ebook. */
    private val progressEbookId: Int get() = if (isStandalone) ebookId else pair?.ebookId ?: 0

    override fun onCreate(savedInstanceState: Bundle?) {
        pairId = intent.getIntExtra(EXTRA_PAIR_ID, 0)
        ebookId = intent.getIntExtra(EXTRA_EBOOK_ID, 0)
        handoffAudio = HandoffAudioAnchor(intent.getLongExtra(EXTRA_HANDOFF_AUDIO_MS, 0L).toInt())
        Log.d(TAG, "onCreate pairId=$pairId ebookId=$ebookId savedState=${savedInstanceState != null}")

        if (savedInstanceState != null && publication == null) {
            // Pass null: restoring the saved fragment state would make
            // FragmentManager reflectively instantiate EpubNavigatorFragment
            // (no zero-arg constructor) and crash before finish() runs.
            super.onCreate(null)
            Log.w(TAG, "Process death detected, finishing")
            finish()
            return
        }

        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_reader)

        // Reported once the pair id is known (issue #597 Track C). Standalone
        // opens (isStandalone, pairId == 0) have no pair for the walkthrough to
        // track and are skipped — TourEvent.ReaderOpened's id is a placeholder
        // for every step built from the static TOUR script anyway (see
        // TourEvent.matchesKind), so this only needs to fire, not carry a real id.
        if (!isStandalone && pairId != 0) {
            // The reader is one of the two places that keeps a streamed
            // pair's sync map alive while its book is actually open (issue
            // #678) — unregistered in onDestroy. Registered here rather than
            // in loadPublication/prefetchBeforeRestore: this needs to cover
            // the whole time the pair is open, not only the fetch at open.
            SyncMapInUse.register(pairId)
            tourController.onEvent(TourEvent.ReaderOpened(pairId))
            // "Here, and still loading" (issue #652) — parsing a full-length book before the
            // navigator is ready took 41 s on the emulator, and a screen the tour has heard
            // nothing from is capped at 30 s. Settled is reported in registerReaderPageAnchor.
            tourRegistry.setSettled(TourScreen.Reader, false)
        }

        // The two nav requests TourController can only make of the reader
        // itself (issue #597 §5): showing the bars for the "tap the middle of
        // the page" step, and skipping the sentence-sync step onto the
        // existing page-level sync when the tour is degraded offline.
        tourNavJob = lifecycleScope.launch {
            tourController.nav.collect { nav ->
                when (nav) {
                    TourNav.ShowReaderBars -> showBarsForTour()
                    TourNav.SkipToToolbarSync -> checkReadinessThenSyncAudioToPage()
                    // The tour moved on to a Main screen: popping the reader
                    // route in the nav host does not close this Activity.
                    TourNav.PopToMain -> finish()
                    // Back out of the reader (issue #788): onto Details, which sits under this
                    // Activity's route, or onto the player, which the nav host opens itself.
                    TourNav.CloseReader -> finish()
                    is TourNav.OpenPlayer -> finish()
                    else -> Unit
                }
            }
        }

        displaySettings.load()
        edgeTapSettings.load()
        initViews()
        applyWindowInsets()
        // Install before the navigator exists — the wrapper sits at the content
        // root and intercepts selection ActionModes from any future WebView.
        installSelectionInterceptor()
        // A style change from Display Settings re-marks the sentence being followed.
        readAlongSettings.setListener {
            readAlong.currentPoint?.let { applyReadAlongDecoration(it) }
        }
        loadPublication()
    }

    private fun initViews() {
        topBar = findViewById(R.id.top_bar)
        bottomBar = findViewById(R.id.bottom_bar)
        toolbar = findViewById(R.id.toolbar)
        progressText = findViewById(R.id.progress_text)
        progressSlider = findViewById(R.id.progress_slider)
        readAlongBar = findViewById(R.id.read_along_bar)
        readAlongPlay = findViewById(R.id.btn_read_along_play)
        readAlongTime = findViewById(R.id.read_along_time)
        backToAudio = findViewById(R.id.btn_back_to_audio)
        readAlongPlay.setOnClickListener { toggleReadAlongPlayback() }
        backToAudio.setOnClickListener { onBackToAudioTapped() }

        // Tap to cycle percent / pages / chapter / time (issue #730); the mode persists.
        // Reported to the tour (issue #743, TourEvent.ReaderProgressModeChanged) on every
        // tap regardless of what's currently showing — the "…" pending state and the
        // fallback notice are just this same text at different moments, and a mode change
        // during either must still advance the reader_progress step.
        progressText.setOnClickListener {
            progressState.cycle()
            renderProgress()
            tourController.onEvent(TourEvent.ReaderProgressModeChanged)
        }

        // Back button
        toolbar.setNavigationIcon(androidx.appcompat.R.drawable.abc_ic_ab_back_material)
        toolbar.setNavigationOnClickListener {
            saveCurrentPosition()
            finish()
        }

        // Menu items: Font Settings, Table of Contents
        toolbar.inflateMenu(R.menu.reader_toolbar)
        // A standalone ebook has no audiobook to switch to (issue #169). Hide
        // the control rather than leave it to fall through to a no-op — an
        // action that does nothing is the same defect class this issue is
        // about, just one screen further in.
        if (isStandalone) {
            toolbar.menu.findItem(R.id.action_switch_audio)?.isVisible = false
            // Likewise nothing to follow without audio (issue #762).
            toolbar.menu.findItem(R.id.action_read_along)?.isVisible = false
        }
        toolbar.menu.findItem(R.id.action_read_along)?.icon?.alpha = READ_ALONG_ICON_OFF_ALPHA
        toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_read_along -> { toggleReadAlong(); true }
                R.id.action_switch_audio -> { checkReadinessThenSyncAudioToPage(); true }
                R.id.action_font_settings -> { showDisplaySettings(); true }
                else -> false
            }
        }

        initOverlayHost()

        // Progress slider
        progressSlider.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onStartTrackingTouch(seekBar: SeekBar?) { isSeeking = true }
            override fun onStopTrackingTouch(seekBar: SeekBar?) {
                isSeeking = false
                val progress = (seekBar?.progress ?: 0) / 1000.0
                goToProgress(progress)
            }
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser) {
                    val pct = (progress / 10.0).toInt()
                    progressText.text = "$pct%"
                    // The indicator's accessible name follows the drag (issue #730).
                    progressText.contentDescription = "Reading progress: $pct%"
                }
            }
        })
    }

    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(android.R.id.content)) { _, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            // Set status bar spacer height
            findViewById<View>(R.id.status_bar_spacer).layoutParams =
                (findViewById<View>(R.id.status_bar_spacer).layoutParams as ViewGroup.LayoutParams).apply {
                    height = systemBars.top
                }
            // Set navigation bar spacer height
            findViewById<View>(R.id.nav_bar_spacer).layoutParams =
                (findViewById<View>(R.id.nav_bar_spacer).layoutParams as ViewGroup.LayoutParams).apply {
                    height = systemBars.bottom
                }
            insets
        }
    }

    /**
     * Wraps a publication's container so every XHTML/HTML entry gets its
     * self-closing `<head/>` rewritten on the way out — issue #373.
     *
     * Readium 3.1.2 has no public "resource transformer" registry. What it does
     * have is `PublicationOpener.open(onCreatePublication = ...)`, which hands over
     * the `Publication.Builder` after parsing and before building, and a
     * `TransformingContainer` in readium-shared that decorates every entry a
     * container hands out. Replacing `builder.container` with one is therefore
     * the supported interposition point, and it sits *below* the navigator's
     * `WebViewServer`, so the injector sees the rewritten bytes.
     *
     * Non-HTML entries are returned untouched — the transform is not even
     * constructed for them — so images, CSS and fonts stream exactly as before.
     * See [normalizeEpubHead] for what the rewrite does and why it is this
     * narrow.
     */
    private fun normalizeHeads(container: Container<Resource>): Container<Resource> =
        TransformingContainer(container) { url: Url, resource: Resource ->
            if (isHtmlLikeExtension(url.extension?.value)) {
                HeadNormalizingResource(resource)
            } else {
                resource
            }
        }

    /**
     * One XHTML entry, with its `<head/>` rewritten.
     *
     * `cacheBytes` is stated rather than left to its default because it is
     * load-bearing here: `WebViewServer` streams the response through
     * `asInputStream`, which asks for the length and then reads in chunks, and
     * an uncached `TransformingResource` would re-read and re-transform the
     * whole resource for every one of those calls.
     */
    private class HeadNormalizingResource(resource: Resource) :
        TransformingResource(resource, cacheBytes = true) {
        override suspend fun transform(
            data: Try<ByteArray, ReadError>,
        ): Try<ByteArray, ReadError> = data.map { normalizeEpubHeadBytes(it) }
    }

    private fun loadPublication() {
        lifecycleScope.launch {
            try {
                // Two entry modes (issue #169). A standalone ebook has no pair
                // row to look up and no sync map to cache; everything past this
                // block — Readium, the restore ladder, the save gate — is the
                // same either way, which is why only the lookup branches.
                val ebookFile = if (isStandalone) {
                    val book = repository.getEbookById(ebookId) ?: run {
                        Log.e(TAG, "Ebook not found for ebookId=$ebookId")
                        finish()
                        return@launch
                    }
                    standaloneEbook = book
                    Log.d(TAG, "Standalone ebook: ${book.title}")
                    toolbar.title = book.title
                    repository.getStandaloneEbookFile(book)
                } else {
                    pair = repository.getPairById(pairId)
                    val bookPair = pair ?: run {
                        Log.e(TAG, "Book pair not found for pairId=$pairId")
                        finish()
                        return@launch
                    }
                    Log.d(TAG, "Book pair: ${bookPair.ebookTitle}")
                    toolbar.title = bookPair.ebookTitle
                    repository.getEbookFile(bookPair)
                }
                if (!ebookFile.exists()) {
                    Log.e(TAG, "Ebook file does not exist: ${ebookFile.absolutePath}")
                    finish()
                    return@launch
                }

                val httpClient = DefaultHttpClient()
                val assetRetriever = AssetRetriever(contentResolver, httpClient)

                val fileUrl = ebookFile.toUri().toAbsoluteUrl()
                    ?: run { Log.e(TAG, "Failed to create URL"); finish(); return@launch }

                val asset = assetRetriever.retrieve(fileUrl).getOrNull()
                    ?: run {
                        val ext = ebookFile.extension.uppercase()
                        val msg = if (ext != "EPUB") {
                            "This book is a .$ext file. Only EPUB format is supported by the reader."
                        } else {
                            "Failed to open the book file. It may be corrupted — try re-downloading."
                        }
                        Log.e(TAG, "Failed to retrieve asset: ${ebookFile.name}")
                        MaterialAlertDialogBuilder(this@ReaderActivity)
                            .setTitle("Cannot Open Book")
                            .setMessage(msg)
                            .setPositiveButton("OK") { _, _ -> finish() }
                            .show()
                        return@launch
                    }

                val parser = DefaultPublicationParser(
                    context = this@ReaderActivity,
                    httpClient = httpClient,
                    assetRetriever = assetRetriever,
                    pdfFactory = null,
                )

                // `onCreatePublication` is the one hook Readium 3.1.2 gives us
                // between parsing and building: it hands over the
                // `Publication.Builder`, whose `container` can be swapped for a
                // wrapper. There is no separate "resource transformer"
                // registry in this version — `TransformingContainer` IS the
                // supported way to interpose. See [normalizeHeads].
                //
                // The hook MUST be passed to `open(...)`, not to the
                // `PublicationOpener(...)` constructor. Both accept an
                // `onCreatePublication`, but in 3.1.2 (and upstream `develop`
                // at the time of writing) `open()` invokes its own parameter
                // twice and never calls the constructor's copy — the parameter
                // shadows the property inside `builder.apply { }`. Wired
                // through the constructor, the container swap silently never
                // happens and every `<head/>` book still fails; verified on an
                // emulator with a Calibre EPUB. `ReaderActivityReadiumHookTest`
                // pins the call shape.
                val pub = PublicationOpener(publicationParser = parser)
                    .open(
                        asset,
                        allowUserInteraction = false,
                        onCreatePublication = { container = normalizeHeads(container) },
                    )
                    .getOrNull()
                    ?: run { Log.e(TAG, "Failed to open publication"); finish(); return@launch }

                Log.d(TAG, "Publication opened: ${pub.metadata.title}, readingOrder=${pub.readingOrder.size} items")
                publication = pub
                ebookFileLength = withContext(Dispatchers.IO) { ebookFile.length() }
                // Before the restore: its chapter rung and seeds are server
                // chapters (issue #804).
                chapterMap = withContext(Dispatchers.IO) {
                    SpineChapterMap.forEpub(ebookFile, pub.readingOrder.map { it.href.toString() })
                }
                Log.d(TAG, "Chapter numbering: ${chapterMap}")
                initProgressIndicator(pub)

                // Pull the server's position before restoring (issue #40) —
                // bounded (issue #167), so a black-hole network can't stall a
                // downloaded book's open; on timeout the local cache stands,
                // like the player. The reader used to read only the local
                // cache, so a position set on another device was never seen
                // and the phone reopened at its own last page — the locator
                // checks below can't catch that, since a stale local bookmark
                // agrees with itself. The helper also refreshes the sync-map
                // cache (issue #55) and fetches the canonical record.
                val fetch = if (isStandalone) {
                    prefetchBeforeRestoreStandalone(repository, ebookId)
                } else {
                    prefetchBeforeRestore(repository, pairId)
                }
                canonicalPosition = fetch.position?.toStoredPosition()
                    ?: if (isStandalone) {
                        // No pair-keyed bookmark row exists for a standalone
                        // ebook; user_progress is the local mirror. It is a
                        // thinner one — no sentence index, no text preview —
                        // so an offline reopen restores by chapter, percent
                        // and the device's own locator hint.
                        repository.getProgressOnce("ebook", ebookId)
                            ?.toStoredPosition(repository.deviceId)
                            .takeIf { !fetch.reachable }
                    } else {
                        repository.getBookmark(pairId)
                            ?.toStoredPosition(repository.deviceId)
                            .takeIf { !fetch.reachable }
                    }
                // See [baselineAudioMs] — issue #682's resume re-anchor needs
                // to know what the reader itself last considered current.
                baselineAudioMs = canonicalPosition?.audioPositionMs

                val initialLocator = getInitialLocator(pub)
                Log.d(TAG, "Initial locator: $initialLocator")
                // The navigator displays SOMETHING even when the restore
                // ladder produced no locator at all — Readium falls back to
                // the start of the publication (the same "failed guess" the
                // policy's LocalMetadataOnly verdict is guarding). Track that
                // fallback target too, so the settle emission for a
                // genuinely-unresolved restore is still recognized as an
                // echo instead of a user page-turn — see [programmaticTarget]
                // and [isProgrammaticEcho].
                programmaticTarget = initialLocator
                    ?: pub.readingOrder.firstOrNull()?.let { pub.locatorFromLink(it) }

                val navigatorFactory = EpubNavigatorFactory(pub)
                supportFragmentManager.fragmentFactory =
                    navigatorFactory.createFragmentFactory(
                        initialLocator = initialLocator,
                        listener = navigatorListener,
                    )

                if (supportFragmentManager.findFragmentByTag(NAV_FRAGMENT_TAG) == null) {
                    supportFragmentManager.beginTransaction()
                        .add(R.id.navigator_container, EpubNavigatorFragment::class.java, Bundle(), NAV_FRAGMENT_TAG)
                        .commitNow()
                }

                navigator = supportFragmentManager
                    .findFragmentByTag(NAV_FRAGMENT_TAG) as? EpubNavigatorFragment
                    ?: run { Log.e(TAG, "Navigator fragment null"); finish(); return@launch }

                navigator?.addInputListener(tapListener)
                navigator?.let { displaySettings.apply(it) }

                // The tour's nudge past the front matter (issue #597 follow-up)
                // — decided in getInitialLocator above; applied only now that
                // the navigator actually exists, so it can't race the
                // fragment's own creation.
                applyTourReaderJump(pub)

                // The walkthrough's reader steps can now trust the page is actually
                // there to spotlight (issue #642) — only for a pair open, since a
                // standalone read is never part of the tour.
                // The page anchor is published here too, not in onCreate (issue #642
                // follow-up): registered that early it resolved the "tap the middle of
                // the page" step over a still-blank page, seconds before a tap could do
                // anything (the tap listener is attached just above). Until now the step
                // stays Pending and the overlay says "One moment…".
                //
                // Settled is reported from inside that same posted block, after the
                // anchor: reported synchronously here it ran ahead of the post by
                // more than the tour's 600 ms settle window on a busy main thread,
                // and the step flashed "not available" for 0.4 s before the anchor
                // landed (seen on the emulator) — the exact flip #642 is about.
                if (!isStandalone) registerReaderPageAnchor()

                Log.d(TAG, "Navigator ready, starting position tracking")
                startPositionTracking()
                navigator?.let { startPageCountTriggers(it) }
                // Wrap WebView's parent so we can intercept the floating selection
                // ActionMode at creation time (no-op if already installed in onCreate).
                installSelectionInterceptor()



            } catch (e: Exception) {
                Log.e(TAG, "Error loading publication", e)
                finish()
            }
        }
    }

    private suspend fun getChapterPlainText(chapterIndex: Int): String? {
        // Return cached value if available
        if (chapterTextCache.containsKey(chapterIndex)) return chapterTextCache[chapterIndex]
        
        val pub = publication ?: return null
        if (chapterIndex !in pub.readingOrder.indices) return null
        val link = pub.readingOrder[chapterIndex]
        return kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            try {
                val resource = pub.get(link) ?: return@withContext null
                val bytes = resource.read().getOrNull() ?: return@withContext null
                val text = String(bytes)
                android.util.Log.d(TAG, "Raw HTML length for ${link.href}: ${text.length}, First 100 chars: ${text.take(100).replace('\n', ' ')}")
                val plainText = org.jsoup.Jsoup.parse(text).text()
                android.util.Log.d(TAG, "Jsoup text length: ${plainText.length}. First 50: ${plainText.take(50)}")
                chapterTextCache[chapterIndex] = plainText
                plainText
            } catch (e: Exception) {
                null
            }
        }
    }

    /**
     * The restore ladder's view of the open book (issue #227): the reading
     * order, each item's parsed text (through [getChapterPlainText]'s cache),
     * and the chapter lengths [startPositionTracking] precomputes.
     */
    private val spineSource = object : SpineSource {
        override val spineCount: Int get() = publication?.readingOrder?.size ?: 0
        override val chapterNumbering: SpineChapterMap get() = this@ReaderActivity.chapterNumbering
        override suspend fun plainTextAt(index: Int): String? = getChapterPlainText(index)
        override suspend fun chapterLengths(): LongArray =
            chapterLengthsDeferred?.await() ?: super.chapterLengths()
    }

    /**
     * Executes the ladder [planRestore] produces. Built lazily because the
     * audio rung exists only for a paired book, and which mode this is comes
     * from the intent in onCreate.
     */
    private val restoreExecutor: ReaderRestoreExecutor by lazy {
        ReaderRestoreExecutor(
            spineSource,
            hintDecodes = { decodeHint(it) != null },
            audio = if (isStandalone) null else AudioAnchorSource { audioMs ->
                repository.audioToEpubText(pairId, audioMs)
            },
        )
    }

    // ============ Bar toggle ============

    private val tapListener = object : InputListener {
        override fun onTap(event: TapEvent): Boolean {
            handleReaderTap(event)
            return true
        }
    }

    /**
     * Routes a page tap through [decideReaderTapAction] (issue #585): a tap
     * near either edge turns the page via Readium's own `goForward`/
     * `goBackward` — the same primitives a swipe uses, so an edge tap on a
     * chapter's last page crosses into the next chapter exactly like a swipe
     * would — and everything else (the middle 56% of the page, or the whole
     * page when [ReaderEdgeTapSettings.enabled] is off) falls through to the
     * existing bar-toggle behavior.
     *
     * A tap while a text selection's ActionMode is up
     * ([ReaderSelectionController.isSelectionActive]) skips the decision
     * entirely and keeps the reader's pre-#585 behavior (toggle the bars):
     * that tap is the user dismissing the selection and must not also turn
     * the page out from under them.
     */
    private fun handleReaderTap(event: TapEvent) {
        if (selectionController.isSelectionActive) {
            toggleBars()
            return
        }
        val nav = navigator
        val width = nav?.publicationView?.width ?: 0
        if (nav == null || width <= 0) {
            // No navigator, or it hasn't been laid out yet — same fallback
            // the reader always had for any tap.
            toggleBars()
            return
        }
        val xFraction = (event.point.x / width).toDouble().coerceIn(0.0, 1.0)
        val isRtl = nav.overflow.value.readingProgression == ReadingProgression.RTL
        when (decideReaderTapAction(xFraction, isRtl, edgeTapSettings.enabled)) {
            ReaderTapAction.TurnPageBackward -> nav.goBackward(true)
            ReaderTapAction.TurnPageForward -> nav.goForward(true)
            ReaderTapAction.ToggleBars -> toggleBars()
        }
    }

    private fun toggleBars() {
        setBarsVisible(!isBarVisible)
    }

    /**
     * Brings up the toolbar exactly as a centre tap would, for the
     * walkthrough's "tap the middle of the page" step (`TourNav.ShowReaderBars`,
     * issue #597 §5) — the tour shows the bars itself after its anchor-timeout
     * window elapses rather than leaving the user stuck on a step whose
     * control never appears.
     */
    fun showBarsForTour() {
        setBarsVisible(true)
    }

    private fun setBarsVisible(visible: Boolean) {
        isBarVisible = visible
        val visibility = if (visible) View.VISIBLE else View.GONE
        topBar.visibility = visibility
        bottomBar.visibility = visibility
        if (visible) {
            tourController.onEvent(TourEvent.ReaderBarsShown)
            registerSwitchToAudioAnchor()
            registerFollowAudioAnchor()
            registerReaderProgressAnchor()
        } else {
            tourRegistry.clear(TourAnchor.ReaderSwitchToAudio)
            tourRegistry.clear(TourAnchor.ReaderFollowAudio)
            // progress_text lives in bottom_bar (issue #743): hidden, it has no bounds
            // worth spotlighting, same reasoning as ReaderSwitchToAudio above.
            tourRegistry.clear(TourAnchor.ReaderProgress)
        }
    }

    /**
     * Publishes the toolbar's "Switch to Audio" action as the
     * [TourAnchor.ReaderSwitchToAudio] anchor (issue #597 §2), in window
     * coordinates the same way `Modifier.tourAnchor` does for every Compose
     * screen. Posted rather than read synchronously: this runs right after
     * `topBar.visibility` flips to VISIBLE, and the toolbar's menu (inflated
     * once, in `initViews`) needs a layout pass before its action item view
     * exists to look up — `toolbar.post` runs after that pass completes.
     * A standalone open hides the action entirely (`initViews`), so there is
     * nothing to find; `findViewById` then returns null and this is a no-op,
     * same as a step whose anchor never shows up on this build.
     */
    private fun registerSwitchToAudioAnchor() {
        toolbar.post {
            val itemView = toolbar.findViewById<View>(R.id.action_switch_audio) ?: return@post
            tourRegistry.set(TourAnchor.ReaderSwitchToAudio, itemView.windowRect())
        }
    }

    /**
     * Publishes the toolbar's "Follow audio" action as [TourAnchor.ReaderFollowAudio]
     * (issue #764), built the same way as [registerSwitchToAudioAnchor]. A standalone
     * open hides the item (`initViews`), so `findViewById` returns null and this is a
     * no-op: the anchor never registers and the tour's step degrades as it does for
     * Switch to Audio.
     */
    private fun registerFollowAudioAnchor() {
        toolbar.post {
            val itemView = toolbar.findViewById<View>(R.id.action_read_along) ?: return@post
            tourRegistry.set(TourAnchor.ReaderFollowAudio, itemView.windowRect())
        }
    }

    /**
     * Re-publishes [TourAnchor.ReaderProgress] once `bottom_bar` (its parent) has actually
     * become visible (issue #743) — same reasoning as [registerSwitchToAudioAnchor]: the
     * view exists throughout, but its bounds are only real once a layout pass has run with
     * the bar shown. [registerReaderPageAnchor]'s own publish, made while the bar was still
     * `GONE`, is a no-op that this corrects the moment the user taps the page.
     */
    private fun registerReaderProgressAnchor() {
        progressText.post {
            tourRegistry.set(TourAnchor.ReaderProgress, progressText.windowRect())
        }
    }

    /**
     * Publishes the navigator container's window bounds as [TourAnchor.ReaderPage]
     * (issue #597 §2) once it has been laid out, so the walkthrough's first
     * reader step ("tap the middle of the page") and the selection step (which
     * reuses this anchor — see `READER_SELECTION_STEP_ID` in `TourScript.kt`)
     * have a hole to spotlight. The progress text's bounds go out as
     * [TourAnchor.ReaderProgress] in the same post (issue #743) — that anchor
     * is the whole view, not whatever it happens to say, so the `reader_progress`
     * step spotlights the same hole whether the text reads a mode, "…" while
     * pages are still counting, or the print-page fallback notice. `bottom_bar`
     * (and this view with it) starts `GONE`, so this first publish is a no-op
     * until the bars actually show — [setBarsVisible] republishes it then.
     */
    private fun registerReaderPageAnchor() {
        val container = findViewById<View>(R.id.navigator_container)
        container.post {
            tourRegistry.set(TourAnchor.ReaderPage, container.windowRect())
            tourRegistry.set(TourAnchor.ReaderProgress, progressText.windowRect())
            tourRegistry.setSettled(TourScreen.Reader, true)
        }
    }

    /** This view's bounds in window coordinates, as the tour's anchor registry stores them. */
    private fun View.windowRect(): ComposeRect {
        val location = IntArray(2)
        getLocationInWindow(location)
        return ComposeRect(
            left = location[0].toFloat(),
            top = location[1].toFloat(),
            right = (location[0] + width).toFloat(),
            bottom = (location[1] + height).toFloat(),
        )
    }

    private fun switchToAudio() {
        saveCurrentPosition()
        setResult(RESULT_SWITCH_TO_AUDIO)
        finish()
    }

    // ============ Navigation listener ============

    private val navigatorListener = object : EpubNavigatorFragment.Listener {
        override fun onExternalLinkActivated(url: AbsoluteUrl) {
            try {
                startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url.toString())))
            } catch (_: Exception) {}
        }

        /**
         * The escape hatch (issue #373). A resource that fails to load leaves
         * the WebView on Chromium's error page, which runs none of Readium's
         * injected JavaScript — so the tap that normally reveals the toolbar
         * and the swipe that normally turns the page both do nothing, and the
         * reader is a dead end with only the system Back button.
         *
         * Readium calls this from `WebViewServer` on a background thread, so
         * everything the response touches is posted to the UI thread.
         */
        override fun onResourceLoadFailed(href: Url, error: ReadError) {
            Log.e(TAG, "Resource failed to load: $href ($error)")
            runOnUiThread { handleResourceLoadFailed(href) }
        }
    }

    private fun handleResourceLoadFailed(href: Url) {
        val pub = publication
        val spineHrefs = pub?.readingOrder?.map { it.href.toString() }.orEmpty()
        // Prefer the failing href's own spine index; fall back to wherever the
        // navigator thinks it is, for an href that is not in the reading order
        // at all (a resource reached from a link, say).
        val spineIndex = spineIndexForHref(spineHrefs, href.toString())
            .takeIf { it >= 0 }
            ?: navigator?.currentLocator?.value
                ?.let { spineIndexForHref(spineHrefs, it.href.toString()) }
            ?: -1
        val action = resourceFailurePolicy.onResourceLoadFailed(
            href = href.toString(),
            barsVisible = isBarVisible,
            hasNextResource = hasNextResource(spineIndex, spineHrefs.size),
        ) ?: return

        // Bars first: even with no next chapter to skip to, the user needs the
        // toolbar back to leave the book or open display settings.
        if (action.revealBars) setBarsVisible(true)

        val label = action.nextChapterLabel
        // Anchored inside the layout rather than at android.R.id.content so
        // Snackbar walks up to the CoordinatorLayout that is activity_reader's
        // root and gets its normal placement and swipe-to-dismiss.
        val snackbar = Snackbar.make(
            findViewById(R.id.navigator_container),
            action.message,
            if (label != null) Snackbar.LENGTH_INDEFINITE else Snackbar.LENGTH_LONG,
        )
        if (label != null) {
            snackbar.setAction(label) {
                val next = pub?.readingOrder?.getOrNull(spineIndex + 1)
                    ?: pub?.readingOrder?.firstOrNull()
                if (next != null) {
                    // go(Link) is the public go-forward-to-next-resource in
                    // Readium 3.1.2; goForward() would have to run inside the
                    // error page's (absent) JavaScript to do anything.
                    programmaticTarget = pub?.locatorFromLink(next)
                    navigator?.go(next, animated = false)
                }
            }
        }
        snackbar.show()
    }

    // ============ Progress tracking ============

    private fun startPositionTracking() {
        val nav = navigator ?: return

        // Precompute chapter content lengths in the background so the slider can map
        // totalProgression (content-weighted) → correct spine item + progression.
        val pub = publication
        if (pub != null && chapterLengthsDeferred == null) {
            chapterLengthsDeferred = lifecycleScope.async {
                LongArray(pub.readingOrder.size) { i ->
                    getChapterPlainText(i)?.length?.toLong() ?: 1000L
                }
            }
        }

        // Start the throttle window at "now" rather than 0 so the first
        // locator the navigator emits — which is just the position we
        // restored — isn't written straight back to the server. Echoing it
        // is pointless when the restore worked, and destructive when it
        // didn't: a restore that lands on page one would otherwise
        // overwrite a real position from another device with chapter 0.
        // A class field (see [lastSaveTime]) rather than local to this
        // coroutine so [reanchorAfterResume] can reset it too.
        lastSaveTime = System.currentTimeMillis()
        positionSaveJob?.cancel()
        positionSaveJob = lifecycleScope.launch {
            nav.currentLocator.collect { locator ->
                // A locator emission means a resource actually rendered (an
                // error page runs no Readium JavaScript and emits nothing), so
                // clear the "already told the user about this one" latch —
                // issue #373.
                resourceFailurePolicy.onResourceDisplayed()

                // Update progress UI
                updateProgressUI(locator)
                val shownAtMs = System.currentTimeMillis()
                // The page count starts once the first locator has settled
                // (issue #730), so it never competes with the open itself.
                pageCountStarted = true
                if (!pageLayoutCaptured && pageCountTriggerJob?.isActive != true) requestPageCount()

                // The player's Read along entry (issue #762): start following
                // once the first locator has settled, so the restore is not
                // mistaken for a manual turn. The request is asynchronous, so
                // this emission itself is still handled as before.
                if (!readAlongRequested && !isStandalone &&
                    intent.getBooleanExtra(EXTRA_READ_ALONG, false)
                ) {
                    readAlongRequested = true
                    requestFollowing(FollowStart.KeepAudio)
                }

                // A configuration change (rotation, dark-mode toggle, split
                // screen — handled in place since issue #163) re-lays out the
                // WebView, which re-emits a recomputed locator with NO user
                // input — often quantized to the top of the chapter. Adopting
                // it as the new programmatic target and skipping the save
                // keeps the re-layout from regressing the anchor; the user's
                // next real page turn saves normally.
                if (System.currentTimeMillis() - lastConfigChangeAtMs < RELAYOUT_ECHO_WINDOW_MS) {
                    programmaticTarget = locator
                    probeLivePage(shownAtMs, userTurn = false)
                    return@collect
                }

                // Readium's currentLocator emits again right after ANY
                // programmatic display settles (initial restore, or a
                // navigator.go(...) call) — same href, a recomputed
                // progression, but no user input. Only an emission that does
                // NOT match the last programmatic target is a real page-turn
                // or slider drag; see [isProgrammaticEcho] and
                // [programmaticTarget]. Explicit navigator.go(...) call sites
                // (goToProgress) call onUserNavigation() themselves so a
                // user-initiated jump isn't swallowed just because its own
                // echo matches.
                // Following audio (issue #762): Readium echoes our own
                // text-anchored jump as a locator with a progression nobody
                // knows in advance, so the controller tells our echo from the
                // user turning the page by hand.
                when (readAlong.onLocatorEmitted(System.currentTimeMillis())) {
                    ReadAlongController.LocatorVerdict.Echo -> {
                        probeLivePage(shownAtMs, userTurn = false)
                        requestReadAlongDecorationLayout()
                        return@collect
                    }
                    ReadAlongController.LocatorVerdict.Suspect -> {
                        // Readium also emits late settle locators with nobody
                        // touching the page, so ask the page before pausing.
                        // Fall through meanwhile: while following, the save
                        // below is dropped anyway; once confirmed, saves resume.
                        verifySuspectedTurn()
                    }
                    ReadAlongController.LocatorVerdict.Ignored -> Unit
                }

                val target = programmaticTarget
                val isEcho = isProgrammaticEcho(
                    targetHref = target?.href?.toString(),
                    targetProgression = target?.locations?.progression,
                    emittedHref = locator.href.toString(),
                    emittedProgression = locator.locations.progression,
                )
                if (!isEcho) {
                    savePolicy.onUserNavigation()
                }
                // The live page and chapter numbers; a reading-speed sample
                // only for the user's own page turns (issue #730).
                probeLivePage(shownAtMs, userTurn = !isEcho)

                // Debounced position save
                val now = System.currentTimeMillis()
                if (now - lastSaveTime >= SAVE_INTERVAL_MS) {
                    lastSaveTime = now
                    savePosition(locator)
                }
            }
        }
    }


    private fun updateProgressUI(locator: Locator) {
        val pub = publication ?: return
        val totalProg = locator.locations.totalProgression
        progressState.onLocator(
            sectionIndex = ReaderProgressInputs.sectionIndexOf(spineHrefs, locator.href.toString()),
            progression = locator.locations.progression,
            fraction = totalProg,
        )

        if (totalProg != null) {
            val pct = (totalProg * 100).toInt()
            // Find chapter title from publication TOC
            val chapterTitle = pub.tableOfContents
                .lastOrNull { toc -> locator.href.toString().contains(toc.href.toString()) }
                ?.title
            progressState.percentText = if (chapterTitle != null) "$chapterTitle · $pct%" else "$pct%"
            if (!isSeeking) progressSlider.progress = (totalProg * 1000).toInt()
        }
        renderProgress()
    }

    /** Shows [progressState] in the indicator; the slider owns the text while it is dragged. */
    private fun renderProgress() {
        if (isSeeking) return
        val now = System.currentTimeMillis()
        progressText.text = progressState.text(now)
        progressText.contentDescription = progressState.contentDescription()
        // The "no print page count" notice gives way to the ebook count by itself.
        progressText.removeCallbacks(renderProgressRunnable)
        progressState.noticeRemainingMs(now)?.let { progressText.postDelayed(renderProgressRunnable, it) }
    }

    private val renderProgressRunnable = Runnable { renderProgress() }

    private fun goToProgress(progress: Double) {
        val pub = publication ?: return
        val nav = navigator ?: return
        val readingOrder = pub.readingOrder
        if (readingOrder.isEmpty()) return

        lifecycleScope.launch {
            // The same content-weighted mapping the percent rung uses, run
            // the other way: a slider fraction -> spine item + progression.
            val spineTarget = restoreExecutor.targetForProgress(progress) ?: return@launch
            val targetSpineIndex = spineTarget.index
            val targetProgression = spineTarget.progression ?: 0.0

            Log.d(TAG, "goToProgress: ${(progress * 100).toInt()}%% -> spine=$targetSpineIndex, intraProgression=%.3f".format(targetProgression))
            val link = readingOrder[targetSpineIndex]
            val locator = pub.locatorFromLink(link) ?: return@launch
            val target = locator.copy(locations = locator.locations.copy(
                progression = targetProgression.coerceIn(0.0, 1.0)
            ))
            // The user dragging the slider IS navigation — call this
            // explicitly rather than relying on the collector's echo
            // detection, since the emission this produces WILL match
            // [programmaticTarget] (that's the point of setting it below) and
            // would otherwise be silently swallowed as an echo.
            savePolicy.onUserNavigation()
            programmaticTarget = target
            nav.go(target, animated = false)
        }
    }

    /**
     * Applies [tourJumpProgression] (issue #597 follow-up), the one time it is
     * non-null, using the same content-weighted fraction -> spine + progression
     * mapping [goToProgress] uses for the slider: [ReaderRestoreExecutor
     * .targetForProgress] turns the fraction into a spine index and an
     * intra-chapter progression, and [locatorFor] turns that into a Readium
     * `Locator` exactly as the restore ladder's own targets are turned into
     * one. [programmaticTarget] is set first, same as every other explicit
     * `navigator.go(...)` call site, so the settle emission this produces is
     * recognized as an echo rather than misread as the user's own navigation.
     *
     * A no-op whenever [tourJumpProgression] is null — which is the case for
     * every non-tour open, and for a tour open on a book that already has a
     * real saved position (see [tourJumpTarget]: it never returns non-null
     * then, and this method never moves a real reader's place).
     */
    private suspend fun applyTourReaderJump(pub: Publication) {
        val progress = tourJumpProgression ?: return
        tourJumpProgression = null
        val nav = navigator ?: return
        val target = restoreExecutor.targetForProgress(progress) ?: return
        val locator = locatorFor(pub, target) ?: return
        Log.d(TAG, "applyTourReaderJump: jumping tour reader open to ${(progress * 100).toInt()}% -> $locator")
        programmaticTarget = locator
        nav.go(locator, animated = false)
    }

    // ============ Progress indicator (issue #730) ============
    //
    // The pure rules live in ReaderProgressState; this section only feeds it:
    // print data at open, the live page probe per locator, and the hidden page
    // count after the first locator and whenever the layout can have changed.

    /** Print data at open: the stored print page count, refreshed from the server, and the page list. */
    private fun initProgressIndicator(pub: Publication) {
        spineHrefs = pub.readingOrder.map { it.href.toString() }
        val pageList = pub.pageList
        pageListInputs = ReaderProgressInputs.pageList(
            spineHrefs,
            hrefs = pageList.map { it.href.toString() },
            labels = pageList.map { it.title.orEmpty() },
        )
        progressState.setPageList(pageListInputs.entries, pageListInputs.labels)

        val id = progressEbookId
        if (id <= 0) return
        progressState.printPageCount = progressPrefs.printPageCount(id)
        if (!networkMonitor.isOnline.value) return
        lifecycleScope.launch {
            // A failed request keeps the stored count; a success replaces it,
            // and a success with null (cleared on the server) clears it.
            val fresh = repository.fetchPrintPageCount(id).getOrElse { return@launch }
            progressPrefs.setPrintPageCount(id, fresh)
            progressState.printPageCount = fresh
            renderProgress()
        }
    }

    /**
     * Asks the live page which CSS column it shows and which of this section's
     * page-list markers it has passed ([LivePageProbe]), then records the page
     * as shown at [shownAtMs]. A newer locator cancels an older probe.
     */
    private fun probeLivePage(shownAtMs: Long, userTurn: Boolean) {
        probeJob?.cancel()
        val nav = navigator ?: return
        val section = progressState.sectionIndex
        if (section < 0) return
        probeJob = lifecycleScope.launch {
            val script = LivePageProbe.script(pageListInputs.fragmentsIn(section), liveProbeSource)
            // The first locator of an open arrives before the live page can
            // answer, or before Readium has scrolled it to the restored spot,
            // and no second one follows until a page turn: ask again a few
            // times rather than leave the chapter's pages unknown or wrong.
            // Without a settled answer the locator's estimate stands.
            var result: LivePageProbe.Result? = null
            for (wait in PROBE_RETRY_DELAYS_MS) {
                if (wait > 0) delay(wait)
                val answer = try {
                    LivePageProbe.parse(nav.evaluateJavascript(script))
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.w(TAG, "Live page probe failed", e)
                    null
                }
                ensureActive()
                if (answer != null && progressState.settled(answer)) {
                    result = answer
                    break
                }
            }
            // Readium's evaluateJavascript is not cancellable (it ends in a
            // plain suspendCoroutine), so a probe a newer locator cancelled
            // still gets here; stop it now. onProbed also drops an answer for
            // a section the reader has left. Without an answer, the page
            // recorded is the locator's estimate.
            ensureActive()
            val probed = progressState.onProbed(section, result, shownAtMs, userTurn)
            if (!probed.accepted) return@launch
            probed.drift?.let { logPageCountDrift(it) }
            renderProgress()
        }
    }

    /** One line in the app log per section whose live page count differs from the counted one. */
    private fun logPageCountDrift(drift: ReaderProgressState.Drift) {
        val line = "Page count drift: section ${drift.sectionIndex} counted ${drift.counted} pages, " +
            "live reader shows ${drift.probed}"
        // The app log writes to a file when capture is on: off the main thread.
        lifecycleScope.launch(Dispatchers.IO) { diagnosticLogger.i(LogChannel.APP, TAG, line) }
    }

    /** Recounts after a settings change or a size change of the reader, once the first locator is in. */
    private fun startPageCountTriggers(nav: EpubNavigatorFragment) {
        pageCountSettingsJob?.cancel()
        pageCountSettingsJob = lifecycleScope.launch {
            nav.settings.collect {
                if (pageCountStarted) {
                    headCaptureRetries.reset()
                    requestPageCount()
                }
            }
        }
        findViewById<View>(R.id.navigator_container)
            .addOnLayoutChangeListener { _, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom ->
                val resized = right - left != oldRight - oldLeft || bottom - top != oldBottom - oldTop
                if (resized && pageCountStarted) {
                    headCaptureRetries.reset()
                    requestPageCount()
                }
            }
    }

    /**
     * Debounced: a burst of triggers makes one capture. A capture that could
     * not run yet (no laid-out WebView, the page turned mid-capture, the live
     * page not answering) is tried once more, so the first locator's capture is
     * not lost when no further locator follows; after that, each locator
     * retries it until one gets through.
     */
    private fun requestPageCount(retryIfAbsorbed: Boolean = true) {
        pageCountTriggerJob?.cancel()
        pageCountTriggerJob = lifecycleScope.launch {
            delay(PAGE_COUNT_DEBOUNCE_MS)
            capturePageLayoutAndCount()
            if (!pageLayoutCaptured && retryIfAbsorbed) requestPageCount(retryIfAbsorbed = false)
        }
    }

    /**
     * Captures the live reader's layout ([LiveHeadCapture]) and its WebView's
     * size, and takes the counts for that layout from the cache or, on a miss,
     * from the hidden [PageCounterWebView]. A new layout cancels the count
     * running for the old one; the same layout leaves it alone.
     */
    private suspend fun capturePageLayoutAndCount() {
        val nav = navigator ?: return
        val pub = publication ?: return
        val id = progressEbookId.takeIf { it > 0 } ?: return
        val live = liveReaderWebView(nav) ?: return
        val width = live.width
        val height = live.height
        if (width <= 0 || height <= 0) return

        val href = nav.currentLocator.value.href.toString()
        if (!headCaptureRetries.shouldTry(href)) return
        val link = pub.readingOrder.getOrNull(ReaderProgressInputs.sectionIndexOf(spineHrefs, href))
            ?: return headCaptureRetries.failed(href)
        val raw = readResourceText(pub, link) ?: return headCaptureRetries.failed(href)
        val captured = try {
            val result = nav.evaluateJavascript(LiveHeadCapture.script(ReaderProgressInputs.rawHead(raw)))
            // Not cancellable either (see probeLivePage): a trigger job
            // replaced while this ran must stop here, not mark the layout
            // captured.
            currentCoroutineContext().ensureActive()
            LiveHeadCapture.parse(result)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Live head capture failed", e)
            null
        } ?: return headCaptureRetries.failed(href)
        // The head diff is only right against the resource it was read from;
        // requestPageCount tries again.
        if (nav.currentLocator.value.href.toString() != href) return

        val hrefs = pub.readingOrder.map { it.url().toString() }
        val key = pageCountCache.key(
            ebookId = id,
            spineSignature = ReaderProgressInputs.spineSignature(spineHrefs, ebookFileLength),
            style = ReaderProgressInputs.layoutStyle(captured.style),
            widthPx = width,
            heightPx = height,
        )
        pageLayoutCaptured = true
        if (key == countingKey || (key == countedKey && progressState.counts != null)) return

        val cached = pageCountCache.load(id, key)
        if (cached != null && cached.counts.size == hrefs.size) {
            pageCountJob?.cancel()
            countingKey = null
            applyCounts(key, cached)
            return
        }

        pageCountJob?.cancel()
        countingKey = key
        countedKey = null
        // Counts for another layout would be wrong; show "…" until this one is in.
        progressState.counts = null
        renderProgress()
        val pubLang = nav.settings.value.language?.code ?: pub.metadata.languages.firstOrNull()
        pageCountJob = lifecycleScope.launch {
            try {
                val counter = pageCounterSized(pub, width, height)
                val startedAt = SystemClock.elapsedRealtime()
                val result = counter.count(captured.style, captured.head, hrefs, pubLang, captured.dir)
                Log.d(TAG, "Page count: ${result.counts.sum()} pages in ${hrefs.size} resources at ${width}x$height, " +
                    "${SystemClock.elapsedRealtime() - startedAt} ms")
                applyCounts(key, result)
                try {
                    pageCountCache.save(id, key, result)
                } catch (e: Exception) {
                    Log.w(TAG, "Could not cache the page count", e)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Page count failed", e)
                // Let the next locator capture and count again, a bounded
                // number of times per layout.
                val failures = (pageCountFailures[key] ?: 0) + 1
                pageCountFailures[key] = failures
                if (failures <= PAGE_COUNT_MAX_RETRIES) pageLayoutCaptured = false
            } finally {
                if (countingKey == key) countingKey = null
            }
        }
    }

    private fun applyCounts(key: String, counts: Counts) {
        countedKey = key
        progressState.counts = counts
        renderProgress()
    }

    /** A resource's markup, read on the IO dispatcher; null if it cannot be read. */
    private suspend fun readResourceText(pub: Publication, link: Link): String? =
        withContext(Dispatchers.IO) {
            try {
                val resource = pub.get(link) ?: return@withContext null
                try {
                    // In the charset the resource declares, not always UTF-8 (#736).
                    resource.read().getOrNull()?.let { ReaderProgressInputs.decodeText(it) }
                } finally {
                    resource.close()
                }
            } catch (e: Exception) {
                Log.w(TAG, "Could not read ${link.href}", e)
                null
            }
        }

    /**
     * The WebView Readium is showing the book in. Its size, not the navigator
     * container's, is what the counter must match: Readium can pad the
     * container for window insets. Every page the pager holds has the same size.
     */
    private fun liveReaderWebView(nav: EpubNavigatorFragment): WebView? =
        nav.view?.let { findLaidOutWebView(it) }

    private fun findLaidOutWebView(view: View): WebView? {
        if (view is WebView) return view.takeIf { it.width > 0 && it.height > 0 }
        if (view is ViewGroup) {
            for (i in 0 until view.childCount) findLaidOutWebView(view.getChildAt(i))?.let { return it }
        }
        return null
    }

    /** The hidden counter, created on first use in [R.id.page_counter_host] and laid out at [width] x [height]. */
    private suspend fun pageCounterSized(pub: Publication, width: Int, height: Int): PageCounterWebView {
        val counter = pageCounter ?: PageCounterWebView(this, pub).also { created ->
            pageCounter = created
            findViewById<FrameLayout>(R.id.page_counter_host).addView(created, FrameLayout.LayoutParams(width, height))
        }
        if (counter.width != width || counter.height != height) {
            counter.layoutParams = FrameLayout.LayoutParams(width, height)
            withTimeoutOrNull(PAGE_COUNTER_LAYOUT_TIMEOUT_MS) {
                suspendCancellableCoroutine { cont ->
                    counter.doOnNextLayout { if (cont.isActive) cont.resume(Unit) }
                }
            }
        }
        return counter
    }

    // ============ Bookmark save/restore ============

    private suspend fun getInitialLocator(pub: Publication): Locator? {
        return try {
            val steps = withHandoffAnchor(
                planRestore(
                    canonicalPosition,
                    // The stored chapter counts every spine item (issue #804).
                    spineCount = chapterNumbering.spineSize,
                    deviceId = repository.deviceId,
                    hintKind = HINT_READIUM_LOCATOR,
                ),
                // One-shot (issue #682 follow-up) — see [HandoffAudioAnchor].
                // A second call to this function, from
                // [reanchorAfterResume], must plan from canonicalPosition
                // alone: the handoff describes where the audiobook was at
                // OPEN, not what a later re-anchor's fresh fetch just found.
                handoffAudioMs = handoffAudio.consume(),
            )
            Log.d(TAG, "getInitialLocator: plan=${steps.map { it.kind }}")

            // The ladder itself — which rung lands, and where — is
            // ReaderRestoreExecutor's (issue #227); this adapter only turns
            // the answer into a Readium locator and records the outcome.
            val result = restoreExecutor.resolve(steps)
            val target = result.target
            val locator = target?.let { locatorFor(pub, it) }
            if (target is RestoreTarget.Spine && locator != null) {
                target.persistAsHintForAudioMs?.let { audioMs ->
                    // Persist so the next open at this audio position takes the
                    // exact-hint rung instead of redoing this lossy chain.
                    repository.updateBookmarkLocator(pairId, locator.toJSON().toString(), audioMs)
                }
            }
            // Monotonic — see PositionSavePolicy. Recorded only once the
            // locator (and any persist) is in hand, so an exception thrown
            // on the way there is caught below as Unresolved, and a landing
            // already recorded can never be demoted by it.
            //
            // A rung that landed but yields no locator (a spine link Readium
            // cannot address) opens the book at the start; that is not a
            // landing, and reporting it as one would let a full save
            // overwrite the real position with page one. Withhold instead.
            val outcome = if (target != null && locator == null) {
                Log.w(TAG, "getInitialLocator: '${result.landed?.kind}' landed but produced no locator")
                PositionSavePolicy.RestoreOutcome.Unresolved
            } else result.outcome
            savePolicy.onRestoreOutcome(outcome)

            // Issue #597 follow-up: decide the tour nudge right here, now that
            // both halves of tourJumpTarget's "no saved position" input are
            // known — a rung actually landed (Landed) versus the book opening
            // fresh, or landing nowhere the ladder could resolve (Unread /
            // Unresolved both display the first spine item, same as a
            // genuinely unread book). tourController.state.value already
            // reflects the walkthrough's current step: the ReaderOpened event
            // that can advance it onto a Reader-screen step was dispatched
            // synchronously in onCreate, before loadPublication (and this
            // suspend function) ever ran.
            val runningTour = tourController.state.value as? TourState.Running
            tourJumpProgression = tourJumpTarget(
                tourRunningOnReader = runningTour?.step?.screen == TourScreen.Reader,
                tourPairId = runningTour?.pairId,
                thisPairId = pairId,
                // A device-local hint from an earlier open counts as "landed" even
                // after the pair's progress was reset, and it pointed at the cover in
                // practice. A pair the tour will put back afterwards (willCleanUp)
                // has no place worth keeping, so the nudge applies regardless.
                hasSavedPosition = outcome == PositionSavePolicy.RestoreOutcome.Landed &&
                    runningTour?.willCleanUp != true,
            )

            if (locator != null) {
                Log.d(TAG, "getInitialLocator: restored via '${result.landed?.kind}'")
            }
            locator
        } catch (e: Exception) {
            Log.w(TAG, "Error getting initial locator", e)
            // Monotonic — a landed rung earlier in the ladder is not undone
            // by an exception thrown while trying a later one.
            savePolicy.onRestoreOutcome(PositionSavePolicy.RestoreOutcome.Unresolved)
            null
        }
    }

    /** A stored Readium locator hint, or null when the value cannot be displayed. */
    private fun decodeHint(value: String): Locator? =
        runCatching { Locator.fromJSON(org.json.JSONObject(value)) }.getOrNull()

    /**
     * Readium `Locator` construction for a restore target — the one thing
     * the executor deliberately does not do. A spine target with no
     * progression is the item's own locator (the chapter rung's "top of the
     * chapter"); one with a progression replaces the locations wholesale,
     * exactly as the text, percent and audio rungs always have.
     */
    private fun locatorFor(pub: Publication, target: RestoreTarget): Locator? = when (target) {
        is RestoreTarget.Hint -> decodeHint(target.value)
        is RestoreTarget.Spine -> {
            val base = pub.readingOrder.getOrNull(target.index)?.let { pub.locatorFromLink(it) }
            val progression = target.progression
            if (base == null || progression == null) base
            else base.copy(locations = Locator.Locations(progression = progression))
        }
    }

    /**
     * Extract a text preview from a chapter at a given progression, stripping
     * headings and book title.
     *
     * Progression × character count is an estimate: Readium paginates by pixel
     * height in CSS columns, so this does not name the page on screen. That is
     * fine for [savePosition], whose preview is a *restore* anchor searched for
     * as text — but it is why the audio handoff no longer accepts it as a page
     * read (issue #131). The suspend, parse-on-miss variant had no callers left
     * once that changed and was removed.
     *
     * [savePosition]'s FullSave capture (issue #61/#40 fix 2) must run
     * synchronously on the calling thread — no `lifecycleScope.launch`, so a
     * fast close can't cancel it before `saveReaderPosition` is even called —
     * which means it cannot parse a chapter that isn't already sitting in
     * [chapterTextCache] (parsing needs Jsoup + resource IO on a background
     * dispatcher). On a cache miss this logs and returns an empty preview
     * rather than blocking the main thread to parse; the chapter index and
     * locator still describe where the reader is.
     */
    private fun extractTextPreviewFromCache(chapterIndex: Int, progression: Double): String {
        val plainText = chapterTextCache[chapterIndex]
        if (plainText == null) {
            Log.w(TAG, "extractTextPreviewFromCache: chapter $chapterIndex not cached, using empty preview")
            return ""
        }
        return buildTextPreview(plainText, progression, pair?.ebookTitle)
    }

    /**
     * Reading-order index [locator] points at, or -1 if it matches none.
     * Readium's numbering, for use inside the reader only — never sent.
     */
    private fun Publication.readingOrderIndexOf(locator: Locator): Int =
        spineIndexForHref(readingOrder.map { it.href.toString() }, locator.href.toString())

    /**
     * The server's chapter for [locator] (issue #804): what every save,
     * page-to-audio match hint and handoff carries as `epub_chapter`. Differs
     * from [readingOrderIndexOf] by the non-linear spine items before it.
     */
    private fun Publication.serverChapterOf(locator: Locator): Int =
        serverChapterForHref(chapterNumbering, readingOrder.map { it.href.toString() }, locator.href.toString())

    /**
     * Book-level progress (0-100) for the UserProgress record, matching the
     * scale the web reader writes. Uses Readium's totalProgression when the
     * publication provides one; otherwise falls back to the precomputed
     * chapter lengths ONLY if that background computation has already
     * finished (`isCompleted`) — [savePosition]'s capture must not suspend to
     * await it (issue #61/#40 fix 2), so an in-flight computation is treated
     * the same as no chapter-length data at all: the percent is omitted
     * (null) rather than blocking, and the server keeps whatever it has.
     */
    private fun bookPercentFor(locator: Locator, chapterIndex: Int): Float? {
        val deferred = chapterLengthsDeferred
        val lengths = if (deferred != null && deferred.isCompleted) deferred.getCompleted() else LongArray(0)
        return bookProgressPercent(
            totalProgression = locator.locations.totalProgression,
            chapterLengths = lengths,
            spineIndex = chapterIndex,
            chapterProgression = locator.locations.progression ?: 0.0,
        )
    }

    private fun savePosition(locator: Locator) {
        // Dropped, not queued (issue #682): [reanchorAfterResume] is deciding
        // whether the ladder needs to run again and, if so, moving the
        // navigator there. A save landing mid-decision — from this method's
        // own collector caller or from onPause — would write exactly the
        // stale page this mechanism exists to correct, right back over
        // whatever the re-anchor is about to establish.
        if (savesBlockedForReanchor) {
            Log.d(TAG, "savePosition: dropped, re-anchoring after resume")
            return
        }
        // Following the audiobook (issue #762): the player service's heartbeat
        // is the only writer, so the record stays audiobook-sourced. A reader
        // save here would flip it to ebook-sourced every few seconds.
        if (readAlong.isFollowing) {
            Log.d(TAG, "savePosition: dropped, following audio — the service owns the position")
            return
        }
        // A standalone ebook has no pair row; everything below keys off
        // isStandalone instead (issue #169).
        if (!isStandalone && pair == null) return
        val pub = publication ?: return
        // Reading-order index: the text cache, the percent and the start-of-book
        // check are the reader's own. What is saved is [serverChapter].
        val chapterIndex = pub.readingOrderIndexOf(locator).coerceAtLeast(0)
        val serverChapter = pub.serverChapterOf(locator)
        val progression = locator.locations.progression ?: 0.0
        // The hard safety net (issue #61/#40 fix 1b): an Unresolved restore
        // sitting at spine 0 must not FullSave even if userNavigated was
        // (mis)set, since there's no per-emission signal that reliably tells
        // a settle emission apart from a real page-turn. See
        // PositionSavePolicy.verdictForSave.
        val atStartOfBook = isAtStartOfBook(chapterIndex, progression)
        val verdict = savePolicy.verdictForSave(atStartOfBook)
        Log.d(TAG, "savePosition: verdict=$verdict atStartOfBook=$atStartOfBook")

        if (verdict == PositionSavePolicy.SaveVerdict.LocalMetadataOnly) {
            // The restore hasn't resolved yet, so this locator is the ladder's
            // failed guess (spine 0), not a place the user chose. Only the
            // bookmark's source/updatedAt are stamped — see
            // BookSyncRepository.updateBookmarkMetadata — so format routing
            // (resolvePairOpenTarget) still works without risking the
            // chapter-0 data loss a full save here would recreate.
            // Nothing to stamp for a standalone ebook. The metadata write
            // exists so that `source` keeps open-target routing following the
            // session (contract, "The write gate"), and an unpaired ebook has
            // no second format to route between — while the anchor half of the
            // verdict still must not be written. So the correct standalone
            // behaviour here is to write nothing at all, not to reach for a
            // pair-keyed row with a pair id of zero.
            if (isStandalone) return

            lifecycleScope.launch {
                try {
                    repository.updateBookmarkMetadata(pairId, source = "ebook")
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.w(TAG, "Error saving position metadata", e)
                }
            }
            return
        }

        val locatorJson = locator.toJSON().toString()

        // Every exit from the reader saves twice: switchToAudio / the toolbar
        // back button / the home item each call saveCurrentPosition() and then
        // finish(), and finish() runs onPause(), which saves again. Both calls
        // are wanted — the explicit one captures synchronously so a fast close
        // can't cancel it, and onPause covers the exits that never make an
        // explicit call — so the redundant repeat is dropped here instead.
        if (!duplicatePositionFilter.shouldWrite("$chapterIndex@$locatorJson")) {
            Log.d(TAG, "savePosition: identical to the previous write, skipping")
            return
        }

        // Everything from here down is synchronous, main-thread-cheap, and
        // runs BEFORE any coroutine boundary (issue #61/#40 fix 2). The old
        // code ran this whole capture inside lifecycleScope.launch with the
        // Room write at the very end, so a fast close cancelled it at the
        // first suspension point — repository.saveReaderPosition was never
        // even called, and nothing was written; onDestroy's
        // publication?.close() could also degrade an in-flight capture's
        // preview to "". Capturing synchronously means the snapshot is
        // complete before savePosition returns, so activity teardown
        // afterward can't lose it — the handoff to saveReaderPosition (which
        // itself now resolves the sync-point match on the repository's own
        // appScope) is the only thing that crosses a coroutine boundary.
        val textPreview = extractTextPreviewFromCache(chapterIndex, progression)

        if (isStandalone) {
            // No sync map and no bookmark row: the anchor is the chapter, the
            // book percentage, the text preview and this device's locator hint
            // (issue #169). Detached for the same reason the paired path is —
            // the contract requires the final flush to survive teardown, and a
            // back-press cancels this activity's scope mid-request.
            repository.saveReaderPositionStandaloneDetached(
                ebookId = ebookId,
                epubChapter = serverChapter,
                epubTextPreview = textPreview,
                epubProgressPercent = bookPercentFor(locator, chapterIndex),
                epubLocator = locatorJson,
            )
            return
        }

        val snapshot = ReaderPositionSnapshot(
            pairId = pairId,
            chapterIndex = serverChapter,
            locatorJson = locatorJson,
            textPreview = textPreview,
            progressPercent = bookPercentFor(locator, chapterIndex),
            capturedAtMillis = System.currentTimeMillis(),
            // A manual audio-sync write is already in flight for this page
            // (see syncSelectedTextToAudio) — resolving a sync-point match
            // here too could overwrite that fresher, deliberately-chosen
            // audio position with a stale automatic guess.
            //
            // The same reasoning covers a session the user never navigated
            // (issue #477): the page on screen is where the restore ladder put
            // them, not somewhere they chose, so an audio position derived from
            // it can only replace a real one with a guess. A ladder that landed
            // confidently on a title page turned 4h32m of listening into 340ms
            // exactly this way. Turning a single page makes the position the
            // user's own and re-enables the lookup.
            skipSyncPointLookup = sentenceSyncPending || !savePolicy.hasUserNavigated(),
        )
        // See [baselineAudioMs]: kept current as the reader's own saves move
        // it, so a later resume compares audio drift against where THIS
        // reader last left the record, not a stale value from open. The
        // resolved audio position lives inside saveReaderPosition's own Room
        // write (issue #61/#40 fix 2 — sync-point resolution happens there,
        // not here), so it is re-read back from Room once that write lands
        // rather than threaded out through the Job's return value.
        repository.saveReaderPosition(snapshot).invokeOnCompletion { cause ->
            if (cause != null) return@invokeOnCompletion
            lifecycleScope.launch {
                baselineAudioMs = repository.getBookmark(pairId)?.audioPositionMs
            }
        }
    }

    // ============ Manual Sync ============

    /**
     * Gets the visible paragraph text from the current chapter's WebView.
     * Readium CSS uses horizontal CSS columns for pagination; the fragments
     * of the current page sit in [0, innerWidth).
     *
     * Reports *how* it answered (issue #131). The scroll-position fallback is an
     * estimate by character count, which does not track page position under
     * column pagination — the caller must not treat it as a real read.
     *
     * Evaluates through `EpubNavigatorFragment.evaluateJavascript`, not a
     * WebView found by walking the view tree (issue #582): Readium keeps
     * three chapter WebViews alive (previous/current/next), and that walk
     * always returned the first one — the chapter just left after any
     * adjacent-chapter move, not the one on screen. The navigator's own
     * `evaluateJavascript` runs against `resourcePager`'s tracked current
     * page, so it always targets the resource actually visible.
     *
     * This used to also search `document.querySelectorAll('iframe')` for a
     * frame whose `src` matched the chapter's filename, on the theory that
     * Readium pre-rendered adjacent chapters into background iframes of one
     * shared WebView. It does not: Readium 3 gives each chapter — previous,
     * current, next — its own WebView with the resource loaded directly
     * into `document`, no iframes at all. That search could therefore never
     * match and always fell through to the `document` reads below; it is
     * deleted rather than kept as inert dead code (issue #582 follow-up).
     */
    private suspend fun extractVisibleTextFromWebView(): VisibleText {
        val nav = navigator ?: return VisibleText.EMPTY
        val js = """
                (function() {
                    function getVisibleText(doc) {
                        var win = doc.defaultView;
                        var vpW = win ? win.innerWidth : 800;
                        var elems = doc.querySelectorAll('p, li, blockquote');
                        for (var i = 0; i < elems.length; i++) {
                            var el = elems[i];
                            // getClientRects() gives one rect per CSS-column
                            // fragment; getBoundingClientRect() gives their
                            // union, which for a paragraph spanning several
                            // columns overlaps the viewport even when the part
                            // on screen is pages away. The old union test
                            // therefore matched the earliest such paragraph
                            // rather than the visible one (issue #131).
                            var rects = el.getClientRects();
                            for (var r = 0; r < rects.length; r++) {
                                var rect = rects[r];
                                if (rect.width <= 0 || rect.height <= 0) continue;
                                // The current column's fragments sit in
                                // [0, vpW); earlier pages are negative, later
                                // ones past vpW.
                                if (rect.left >= -1 && rect.left < vpW) {
                                    var text = (el.innerText || el.textContent || '').trim();
                                    if (text.length > 20) return text.substring(0, 350);
                                    break;
                                }
                            }
                        }
                        return '';
                    }

                    function scrollBasedText(doc) {
                        // Fallback: use horizontal scroll position to estimate chapter offset
                        var de = doc.documentElement;
                        var sl = de.scrollLeft || 0;
                        var sw = de.scrollWidth || 1;
                        var vw = (doc.defaultView ? doc.defaultView.innerWidth : 0) || 800;
                        var fraction = sw > vw ? sl / (sw - vw) : 0;
                        var body = doc.body ? (doc.body.innerText || '') : '';
                        var pos = Math.floor(body.length * fraction);
                        return body.substring(Math.max(0, pos - 20), pos + 300);
                    }

                    function result(text, source) {
                        return JSON.stringify({ text: text, source: source });
                    }

                    // evaluateJavascript always runs against the current
                    // chapter's own document (see the doc comment above) —
                    // there is nothing else on this page to search.
                    var own = getVisibleText(document);
                    if (own.trim().length > 10) return result(own, 'dom');
                    var ownGuess = scrollBasedText(document);
                    if (ownGuess.trim().length > 10) return result(ownGuess, 'estimated');

                    return result('', 'none');
                })()
            """.trimIndent()
        // The bridge double-encodes our JSON.stringify(...); VisibleText.parse
        // handles both shapes, so the whole decode is one unit-tested step.
        return VisibleText.parse(nav.evaluateJavascript(js))
    }

    /**
     * Wires [R.id.overlay_host] to [ReaderOverlays] (issue #597 Track C —
     * renamed from `transcription_dialog_host`, which held only
     * [TranscriptionStatusDialog] before the walkthrough needed a second,
     * independent overlay in the same spot). [TranscriptionStatusDialog]'s
     * behaviour is unchanged; [ReaderOverlays] additionally renders
     * [com.booksync.ui.tour.TourOverlay] whenever the walkthrough is running a
     * Reader or Player step. Empty content when neither is showing, so the
     * view underneath still receives touches, exactly as before.
     */
    private fun initOverlayHost() {
        findViewById<ComposeView>(R.id.overlay_host).apply {
            setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnViewTreeLifecycleDestroyed)
            setContent {
                val status = pendingSwitchStatus.value
                val online by networkMonitor.isOnline.collectAsState()
                ReaderOverlays(
                    pendingSwitchStatus = status,
                    canTranscribeSwitch = canTranscribeSwitch.value,
                    isOnline = online,
                    onContinueSwitch = {
                        pendingSwitchStatus.value = null
                        syncAudioToPage()
                    },
                    onTranscribeSwitch = {
                        lifecycleScope.launch {
                            repository.addToTranscriptionQueue(pairId)
                                .onSuccess {
                                    android.widget.Toast.makeText(
                                        this@ReaderActivity,
                                        "Added to transcription queue",
                                        android.widget.Toast.LENGTH_SHORT,
                                    ).show()
                                    pendingSwitchStatus.value = repository.readiness(pairId)
                                }
                                .onFailure { e ->
                                    android.widget.Toast.makeText(
                                        this@ReaderActivity,
                                        e.message ?: "Failed to add to queue",
                                        android.widget.Toast.LENGTH_SHORT,
                                    ).show()
                                }
                        }
                    },
                    onDismissSwitch = { pendingSwitchStatus.value = null },
                    tourController = tourController,
                    tourRegistry = tourRegistry,
                )
            }
        }
    }

    /**
     * Gate in front of [syncAudioToPage] (issue #536): a pair with no sync map
     * switched silently to the beginning of the audiobook with no explanation.
     * A ready pair ([BookSyncRepository.readiness] returns null) switches
     * immediately, exactly as before; anything else raises
     * [TranscriptionStatusDialog] via [pendingSwitchStatus] instead.
     */
    private fun checkReadinessThenSyncAudioToPage() {
        lifecycleScope.launch {
            val status = repository.readiness(pairId)
            if (status == null) {
                syncAudioToPage()
            } else {
                canTranscribeSwitch.value = hasMinRole(tokenManager.getRole().first(), "editor")
                pendingSwitchStatus.value = status
            }
        }
    }

    /**
     * The toolbar's "Switch to Audio" action (issue #114).
     *
     * Matches the text actually on screen to a sync point and hands the player
     * that exact second, rather than letting it re-derive a position from the
     * chapter anchor. Every exit still goes through [switchToAudio], so a page
     * that can't be matched — no sync map, no audiobook downloaded, no readable
     * DOM text — behaves exactly as the button did before: it switches, on the
     * anchor, with a toast saying why it wasn't precise.
     */
    private fun syncAudioToPage() {
        val locator = navigator?.currentLocator?.value
        val pub = publication
        if (pair?.audiobookDownloaded != true || locator == null || pub == null) {
            // Nothing to match against — the plain anchor handoff is the whole
            // of what this button used to do, so fall back to it silently.
            switchToAudio()
            return
        }

        // The match hint and the handoff are server chapters (issue #804).
        val serverChapter = pub.serverChapterOf(locator)

        val progression = locator.locations.progression ?: 0.0
        Log.d(TAG, "syncAudioToPage called! locator.href='${locator.href}', progression=$progression")
        Log.d(TAG, "syncAudioToPage: serverChapter=$serverChapter")

        lifecycleScope.launch {
            // Only a real DOM read of the current column is worth seeking on.
            // The extractor's other answer is a character-offset estimate, which
            // under column pagination routinely names text from another page —
            // and a confident seek to the wrong second is worse than the anchor
            // handoff the user would otherwise have got (issue #131).
            val visible = extractVisibleTextFromWebView()
            Log.d(TAG, "syncAudioToPage: source=${visible.source} " +
                "textPreview='${visible.text.take(80)}'")

            val audioMs = if (visible.isPrecise) {
                repository.epubToAudioText(pairId, serverChapter, visible.text)
            } else 0
            // Set before the write, so a savePosition landing in between can't
            // resolve its own sync-point guess over this deliberate match (see
            // ReaderPositionSnapshot.skipSyncPointLookup).
            if (audioMs > 0) sentenceSyncPending = true

            val matched = PageAudioHandoff.apply(
                repository = repository,
                pairId = pairId,
                chapterIndex = serverChapter,
                locatorJson = locator.toJSON().toString(),
                audioMs = audioMs,
            )
            val message = when {
                matched -> "Audio synced to ${formatAudioTime(audioMs.toLong())}"
                visible.isPrecise -> "No matching audio found for this page"
                // Say what actually happened rather than implying a failed
                // match: we never got a usable read of the page.
                else -> "Couldn't read this page — switching on the chapter anchor"
            }
            android.widget.Toast.makeText(this@ReaderActivity, message, android.widget.Toast.LENGTH_SHORT).show()
            switchToAudio()
        }
    }

    // ============ Display Settings ============

    /** Font / theme / spacing preferences and their dialog — see [ReaderDisplaySettings]. */
    private val displaySettings: ReaderDisplaySettings by lazy { ReaderDisplaySettings(this) }

    /** The "turn pages by tapping the edges" preference — see [handleReaderTap]. */
    private val edgeTapSettings: ReaderEdgeTapSettings by lazy { ReaderEdgeTapSettings(this) }

    /** Read-along's highlight-or-underline preference (issue #762). */
    private val readAlongSettings: ReadAlongSettings by lazy { ReadAlongSettings(this) }

    private fun showDisplaySettings() {
        val nav = navigator ?: return
        displaySettings.showDialog(this, nav, edgeTapSettings, readAlongSettings, progressState) { renderProgress() }
    }

    // ============ Text Selection Sync ============
    //
    // The floating-toolbar surgery (intercepting the WebView's ActionMode,
    // trimming noise items, injecting Define / Sync to Audio) lives in
    // ReaderSelectionController (issue #227); the two actions it invokes are
    // below, because they need the navigator, the repository and the
    // hand-off to the player.

    private val selectionController: ReaderSelectionController by lazy {
        ReaderSelectionController(
            this,
            object : ReaderSelectionController.Host {
                // Issue #597 Track C, "Selection sync while streaming": no longer
                // requires a downloaded audiobook — see [selectionSyncAvailable].
                override val syncToAudioAvailable: Boolean
                    get() = selectionSyncAvailable(pair, networkMonitor.isOnline.value)
                override fun onDefine(dismiss: () -> Unit) = defineSelectedWord(dismiss)
                override fun onSyncToAudio(dismiss: () -> Unit) = syncSelectedTextToAudio(dismiss)
                override fun onReadAlong(dismiss: () -> Unit) = readAlongFromSelection(dismiss)
                override fun onSelectionStarted() = tourController.onEvent(TourEvent.ReaderSelectionStarted)
            },
        )
    }

    /** See [ReaderSelectionController.install]. Idempotent; onResume() re-calls as a no-op. */
    private fun installSelectionInterceptor() = selectionController.install()

    /** Defensive fallback — see [ReaderSelectionController.onActionModeStarted]. */
    override fun onActionModeStarted(mode: android.view.ActionMode?) {
        super.onActionModeStarted(mode)
        selectionController.onActionModeStarted(mode)
    }

    /**
     * Look up the first word of the user's selection — Wiktionary first,
     * falling back to dictionaryapi.dev (issue #608) — and present the
     * result in a dialog. Toasts distinguish "no entry for this word" from
     * "the lookup itself failed" (offline, or both sources erroring).
     *
     * Reads the selection from Readium's navigator at the moment Define is
     * tapped, rather than a value cached earlier (issue #582):
     * `currentSelection()` always targets the resource the reader is showing
     * right now, so there is nothing stale to fall back to — an empty
     * current selection means the same as it always did, "nothing is
     * selected".
     *
     * [dismiss] — [ReaderSelectionController.Host.onDefine]'s callback that
     * finishes the ActionMode — is called only after that read completes,
     * not before: finishing the ActionMode tears down the WebView's native
     * selection, and calling it any earlier would race that teardown against
     * the asynchronous `currentSelection()` call and could hand back "" even
     * though the highlight was read a moment before.
     */
    private fun defineSelectedWord(dismiss: () -> Unit) {
        lifecycleScope.launch {
            val highlight = navigator?.currentSelection()?.locator?.text?.highlight
            Log.d(TAG, "defineSelectedWord: currentSelection highlight='$highlight'")
            dismiss()
            val firstToken = firstDefinableToken(defineSelectionText(highlight))

            if (firstToken.isEmpty()) {
                android.widget.Toast.makeText(this@ReaderActivity, "Select a word to define", android.widget.Toast.LENGTH_SHORT).show()
                return@launch
            }
            Log.d(TAG, "Defining word: '$firstToken'")

            val entries = try {
                dictionaryRepository.lookup(firstToken)
            } catch (e: Exception) {
                Log.w(TAG, "Dictionary lookup failed for '$firstToken'", e)
                android.widget.Toast.makeText(
                    this@ReaderActivity,
                    "No definition available",
                    android.widget.Toast.LENGTH_SHORT,
                ).show()
                return@launch
            }
            if (entries.isEmpty()) {
                android.widget.Toast.makeText(
                    this@ReaderActivity,
                    "No definition found for '$firstToken'",
                    android.widget.Toast.LENGTH_SHORT,
                ).show()
                return@launch
            }
            DictionarySheet.show(this@ReaderActivity, firstToken, entries)
        }
    }

    /**
     * Reads the selection from Readium's navigator at click time — see
     * [defineSelectedWord] (issue #582) — rather than a value captured
     * earlier by WebView JavaScript, which could name a chapter the reader
     * had already left. [dismiss] finishes the ActionMode and, exactly as in
     * [defineSelectedWord], is only called after the read completes — see
     * that function's doc for why the ordering matters.
     */
    private fun syncSelectedTextToAudio(dismiss: () -> Unit) {
        lifecycleScope.launch {
            val selection = navigator?.currentSelection()
            val selectedText = selection?.locator?.text?.highlight?.trim().orEmpty()
            dismiss()
            Log.d(TAG, "Selected text: '${selectedText.take(100)}'")

            if (selectionTooShortToSync(selectedText)) {
                android.widget.Toast.makeText(this@ReaderActivity, "Select more text to sync", android.widget.Toast.LENGTH_SHORT).show()
                return@launch
            }

            val locator = selection?.locator ?: return@launch
            val pub = publication ?: return@launch
            val serverChapter = pub.serverChapterOf(locator)
            Log.d(TAG, "syncSelectedText: serverChapter=$serverChapter, text='${selectedText.take(60)}'")

            val outcome = syncSelectionToAudio(
                repository = repository,
                pairId = pairId,
                chapterIndex = serverChapter,
                locatorJson = locator.toJSON().toString(),
                selectedText = selectedText,
            ) { audioMs ->
                Log.d(TAG, "syncSelectedText: matched audioMs=$audioMs (${formatAudioTime(audioMs.toLong())})")
                // Set before the write, so a savePosition landing in between
                // can't resolve its own sync-point guess over this deliberate
                // match (see ReaderPositionSnapshot.skipSyncPointLookup).
                sentenceSyncPending = true
            }
            when (outcome) {
                is SelectionSyncOutcome.Matched -> {
                    val audioMs = outcome.audioMs
                    val timeStr = formatAudioTime(audioMs.toLong())
                    android.widget.Toast.makeText(this@ReaderActivity, "Audio synced to $timeStr", android.widget.Toast.LENGTH_SHORT).show()
                    // Reported before switchToAudio()'s finish() tears the
                    // Activity down (issue #597 §2/§5) — the walkthrough's
                    // sentence-sync step is waiting on exactly this event.
                    tourController.onEvent(TourEvent.ReaderSyncedSelection)
                    // After successful sync, jump straight to the player so the user can continue listening.
                    switchToAudio()
                }
                SelectionSyncOutcome.NoMatch ->
                    android.widget.Toast.makeText(this@ReaderActivity, "No matching audio found", android.widget.Toast.LENGTH_SHORT).show()
            }
        }
    }

    /**
     * "Read along" in the selection toolbar (issue #772): look the selected
     * sentence up in the sync map and start following the audio from there,
     * staying in the reader. Reads the selection from Readium's navigator at
     * click time and calls [dismiss] only afterwards, as [syncSelectedTextToAudio]
     * does. Unlike Sync to Audio this writes no handoff bookmark, leaves the
     * pending audio seek alone, and does not leave the reader: the lookup is
     * made with no rewind, and the start goes through the same readiness gate
     * as the toolbar's Follow audio.
     */
    private fun readAlongFromSelection(dismiss: () -> Unit) {
        lifecycleScope.launch {
            val selection = navigator?.currentSelection()
            val selectedText = selection?.locator?.text?.highlight?.trim().orEmpty()
            dismiss()

            if (selectionTooShortToSync(selectedText)) {
                android.widget.Toast.makeText(this@ReaderActivity, "Select more text to sync", android.widget.Toast.LENGTH_SHORT).show()
                return@launch
            }

            val locator = selection?.locator ?: return@launch
            val pub = publication ?: return@launch
            val serverChapter = pub.serverChapterOf(locator)
            val audioMs = repository.epubToAudioText(pairId, serverChapter, selectedText, rewindMs = 0)
            if (audioMs <= 0) {
                android.widget.Toast.makeText(this@ReaderActivity, "No matching audio found", android.widget.Toast.LENGTH_SHORT).show()
                return@launch
            }
            requestFollowing(FollowStart.AudioMs(audioMs))
        }
    }

    private fun formatAudioTime(ms: Long): String {
        val totalSec = (ms / 1000).toInt()
        val h = totalSec / 3600
        val m = (totalSec % 3600) / 60
        val s = totalSec % 60
        return "%d:%02d:%02d".format(h, m, s)
    }

    // ============ Read-along (issue #762) ============
    //
    // While the audiobook plays, the reader follows the current sentence and
    // turns the page by itself. The decisions live in [ReadAlongController];
    // this section owns the MediaController, the poll and the Readium calls.
    // While following, [savePosition] writes nothing, so the player service's
    // heartbeat stays the only position writer (docs/position-sync-contract.md).

    private fun toggleReadAlong() {
        if (readAlong.state != ReadAlongController.State.Off) {
            stopFollowingAndPause()
        } else {
            requestFollowing(FollowStart.VisiblePage)
        }
    }

    /** Readiness gate shared with Switch to Audio (issue #536): no sync map, no following. */
    private fun requestFollowing(start: FollowStart) {
        if (isStandalone) return
        lifecycleScope.launch {
            val status = repository.readiness(pairId)
            if (status != null) {
                canTranscribeSwitch.value = hasMinRole(tokenManager.getRole().first(), "editor")
                pendingSwitchStatus.value = status
                return@launch
            }
            startFollowing(start)
        }
    }

    private fun startFollowing(start: FollowStart) {
        lifecycleScope.launch {
            val p = pair ?: return@launch
            val points = repository.getSyncPoints(pairId)
            val ctrl = readAlongMediaController ?: connectReadAlongController()
            if (ctrl == null) {
                android.widget.Toast.makeText(
                    this@ReaderActivity, "Audio player unavailable", android.widget.Toast.LENGTH_SHORT,
                ).show()
                return@launch
            }
            // Where the audio starts. A page or selection start goes to an exact
            // sentence, so neither this lookup nor the player's resume rewind
            // may back it up (issue #772).
            val audioMs = when (start) {
                FollowStart.KeepAudio -> 0
                is FollowStart.AudioMs -> start.ms
                FollowStart.VisiblePage -> {
                    // Start where the eye is, not where the audio was: the same
                    // page-to-audio match Switch to Audio performs (issue #114 / #131).
                    val visible = extractVisibleTextFromWebView()
                    val serverChapter = navigator?.currentLocator?.value
                        ?.let { publication?.serverChapterOf(it) } ?: 0
                    if (visible.isPrecise) {
                        repository.epubToAudioText(pairId, serverChapter, visible.text, rewindMs = 0)
                    } else 0
                }
            }
            val wantedId = MediaId.Pair(pairId).value
            val plan = planAudioStart(
                itemLoaded = ctrl.currentMediaItem?.mediaId == wantedId,
                targetMs = audioMs,
                savedMs = repository.getBookmark(pairId)?.audioPositionMs,
            )
            if (audioMs > 0 && !ctrl.playWhenReady) {
                // Only worth arming when a resume is coming: a player that is
                // already set to play never resumes, and the one-shot would
                // then wait for the user's next pause-and-play.
                ctrl.sendCustomCommand(
                    SessionCommand(AudioPlayerService.CMD_SUPPRESS_NEXT_RESUME_REWIND, Bundle()),
                    Bundle(),
                )
            }
            when (plan) {
                is AudioStartPlan.Load -> {
                    val item = PairMediaItems.build(
                        p, repository.localAudioFile(p.audiobookFilename), serverUrlManager.currentUrl,
                    )
                    if (item == null) {
                        Log.w(TAG, "read-along: no audio source for pair $pairId")
                        return@launch
                    }
                    // The start position travels with the item: the session
                    // resolves a new item asynchronously with its own start,
                    // and a seek sent in between is lost (seen on a phone:
                    // Read along began at the top of the book).
                    if (plan.startMs != null) ctrl.setMediaItem(item, plan.startMs) else ctrl.setMediaItem(item)
                    ctrl.prepare()
                }
                is AudioStartPlan.Seek -> plan.seekMs?.let { ctrl.seekTo(it) }
            }
            // playWhenReady, not isPlaying: a player that is buffering after the
            // seek above is already set to play, and a second play() is a
            // COMMAND_PLAY_PAUSE the service would log as a deliberate pause.
            if (!ctrl.playWhenReady) ctrl.play()
            // Already following or paused (a selection can start a run at any
            // time): a fresh controller ends the pause and, with the chip
            // hidden below, leaves exactly one run in Following.
            suspectVerifyJob?.cancel()
            suspectVerifyJob = null
            val words = repository.getSyncPointWords(pairId)
            clearWordMark()
            readAlong = ReadAlongController(points, words)
            readAlong.start(System.currentTimeMillis())
            setFollowToolbar(active = true)
            readAlongBar.visibility = View.VISIBLE
            updateReadAlongPlayButton(ctrl.playWhenReady)
            showBackToAudio(false)
            startReadAlongPoll(ctrl)
            if (words.isEmpty()) {
                // No word timing cached (a map from before the feature, or a
                // failed fetch): ask once. The sentence mark does not wait for it.
                val run = readAlong
                launch {
                    if (repository.ensureSyncPointWords(pairId) && readAlong === run && run.isFollowing) {
                        readAlong.setWords(repository.getSyncPointWords(pairId))
                        readAlong.currentPoint?.let { locateWordMark(it) }
                    }
                }
            }
        }
    }

    /**
     * The toolbar button while following is on (issue #772): stop the audio
     * too. Turning following off hides the reader's only audio controls, so
     * leaving playback running left audio with nothing on screen to stop it.
     * Leaving the reader, or the book ending, still goes through
     * [stopFollowing] alone and does not touch playback.
     */
    private fun stopFollowingAndPause() {
        readAlongMediaController?.let { ctrl ->
            if (ctrl.playWhenReady) {
                ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_USER_PAUSE, Bundle()), Bundle())
                ctrl.pause()
            }
        }
        stopFollowing()
    }

    private fun stopFollowing() {
        readAlongPollJob?.cancel()
        readAlongPollJob = null
        suspectVerifyJob?.cancel()
        suspectVerifyJob = null
        readAlong.stop()
        clearReadAlongDecoration()
        setFollowToolbar(active = false)
        readAlongBar.visibility = View.GONE
        showBackToAudio(false)
        // Playback is not touched here; the toolbar button pauses first, in
        // stopFollowingAndPause.
    }

    private fun setFollowToolbar(active: Boolean) {
        toolbar.menu.findItem(R.id.action_read_along)?.apply {
            title = if (active) "Stop following" else "Follow audio"
            icon?.alpha = if (active) 255 else READ_ALONG_ICON_OFF_ALPHA
        }
    }

    /**
     * Attaches to the already-running [AudioPlayerService], as
     * `PlayerViewModel.connectToService` does. Null when the session cannot
     * be reached (a broken install; the JVM) — read-along then says so and
     * the reader carries on as an ordinary reader.
     */
    private suspend fun connectReadAlongController(): MediaController? {
        val token = try {
            SessionToken(applicationContext, ComponentName(applicationContext, AudioPlayerService::class.java))
        } catch (e: Exception) {
            Log.w(TAG, "read-along: session token failed", e)
            return null
        }
        val future = MediaController.Builder(applicationContext, token).buildAsync()
        val ctrl = try {
            suspendCancellableCoroutine<MediaController> { cont ->
                future.addListener(
                    { cont.resumeWith(runCatching { future.get() }) },
                    androidx.core.content.ContextCompat.getMainExecutor(this),
                )
                cont.invokeOnCancellation { MediaController.releaseFuture(future) }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "read-along: controller connect failed", e)
            return null
        }
        ctrl.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                when (playbackState) {
                    Player.STATE_ENDED -> stopFollowing()
                }
            }

            // playWhenReady rather than isPlaying, so a rebuffer does not flip
            // the button to "play" while the audio is still meant to run.
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                updateReadAlongPlayButton(playWhenReady)
            }
        })
        readAlongMediaController = ctrl
        return ctrl
    }

    private fun updateReadAlongPlayButton(isPlaying: Boolean) {
        readAlongPlay.setImageResource(
            if (isPlaying) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play,
        )
        readAlongPlay.contentDescription = if (isPlaying) "Pause audio" else "Play audio"
    }

    /** Samples the audio position while the reader is on screen; stops with the activity's STARTED state. */
    private fun startReadAlongPoll(ctrl: MediaController) {
        readAlongPollJob?.cancel()
        readAlongPollJob = lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    val audioMs = ctrl.currentPosition.toInt()
                    readAlongTime.text = formatAudioTime(audioMs.toLong())
                    readAlong.onAudioPosition(audioMs, System.currentTimeMillis())
                        .forEach { onReadAlongAction(it) }
                    delay(READ_ALONG_POLL_MS)
                }
            }
        }
    }

    /** What a poll tick yields: a new sentence to mark, or a new word within it. */
    private fun onReadAlongAction(action: ReadAlongController.Action) {
        when (action) {
            is ReadAlongController.Action.Decorate -> onReadAlongDecorate(action.point)
            is ReadAlongController.Action.Word -> onReadAlongWord(action)
            is ReadAlongController.Action.Jump -> Unit // only the visibility check yields a jump
        }
    }

    /**
     * The audio reached a new word (issue #836). A token below zero (before the
     * first word, or a sentence with no word timing) only removes the mark.
     * Otherwise it is drawn when the page search found exactly as many words as
     * the timing has: a count that differs means the page text and the
     * server's text disagree, and a mark on the wrong word is worse than none.
     * A word that arrives before the search has answered is remembered and
     * drawn by [locateWordMark] when it does.
     */
    private fun onReadAlongWord(action: ReadAlongController.Action.Word) {
        val nav = navigator ?: return
        val starts = readAlong.wordStartsFor(action.point)
        if (action.tokenIndex < 0 || starts == null) {
            wordMarkWanted = -1
            lifecycleScope.launch { nav.evaluateJavascript(wordMarkClearScript()) }
            return
        }
        wordMarkWanted = action.tokenIndex
        if (wordMarkTokenCount == starts.size && sameSentencePoint(wordMarkPoint, action.point)) {
            lifecycleScope.launch { nav.evaluateJavascript(wordMarkSetScript(action.tokenIndex)) }
        }
    }

    private fun sameSentencePoint(a: SyncPointEntity?, b: SyncPointEntity?): Boolean =
        a != null && b != null && a.epubChapter == b.epubChapter && a.epubSentenceIndex == b.epubSentenceIndex

    /**
     * Builds the DOM ranges for [point]'s words on the current page (see
     * `WordMark.kt`) and, when the page answers with as many as the timing has,
     * draws the word the audio is on. Called when the sentence changes and again
     * once a jump has settled, since a jump into another chapter loads a new
     * document. Removes the previous sentence's mark first, so it never lingers
     * while the search runs. A sentence without word timing locates nothing.
     */
    private fun locateWordMark(point: SyncPointEntity) {
        val sameSentence = sameSentencePoint(wordMarkPoint, point)
        if (!sameSentence) wordMarkWanted = -1
        wordMarkPoint = point
        wordMarkTokenCount = 0
        val nav = navigator ?: return
        val starts = readAlong.wordStartsFor(point)
        val quote = readAlong.quotes.quoteFor(point)
        val plan = quote?.let { wordMarkPlan(point.epubTextPreview, it) }
        lifecycleScope.launch {
            // Re-locating the same sentence (after a jump) keeps its mark up.
            if (!sameSentence) nav.evaluateJavascript(wordMarkClearScript())
            if (starts == null || plan == null) return@launch
            val answer = nav.evaluateJavascript(
                wordMarkLocateScript(plan.quote.highlight, plan.quote.before, plan.quote.after, plan.tokenRanges),
            )
            val built = answer?.trim()?.toIntOrNull() ?: 0
            // A newer sentence may have been located while this one was answering.
            if (!sameSentencePoint(wordMarkPoint, point)) return@launch
            wordMarkTokenCount = built
            if (built != starts.size) {
                Log.d(
                    TAG,
                    "read-along: word mark skipped for chapter ${point.epubChapter} sentence " +
                        "${point.epubSentenceIndex} (page ranges $built, timed words ${starts.size})",
                )
                return@launch
            }
            if (wordMarkWanted >= 0) nav.evaluateJavascript(wordMarkSetScript(wordMarkWanted))
        }
    }

    /** Removes the word mark and forgets which sentence it was located for. */
    private fun clearWordMark() {
        wordMarkPoint = null
        wordMarkTokenCount = 0
        wordMarkWanted = -1
        val nav = navigator ?: return
        lifecycleScope.launch { nav.evaluateJavascript(wordMarkClearScript()) }
    }

    /** The audio reached a new sentence: mark it, then turn the page if it is not on screen. */
    private fun onReadAlongDecorate(point: SyncPointEntity) {
        applyReadAlongDecoration(point)
        locateWordMark(point)
        lifecycleScope.launch {
            val quote = readAlong.quotes.quoteFor(point) ?: return@launch
            val visible = isSentenceVisible(
                quote, "chapter ${point.epubChapter} sentence ${point.epubSentenceIndex}",
            )
            readAlong.onSentenceVisibility(point, visible, System.currentTimeMillis())
                ?.let { jumpToSentence(it.point) }
        }
    }

    /**
     * Marks [point]'s sentence with one Readium decoration in its own group;
     * re-applying replaces the previous mark. Readium anchors it on the
     * locator's text quote ([sentenceLocator]). While following is paused for
     * a manual page turn nothing calls this, so the mark stays on the sentence
     * that was current until [onBackToAudioTapped] applies the next one.
     */
    private fun applyReadAlongDecoration(point: SyncPointEntity) {
        val nav = navigator ?: return
        val locator = sentenceLocator(point) ?: return
        val style = when (readAlongSettings.style) {
            ReadAlongStyle.UNDERLINE -> Decoration.Style.Underline(tint = readAlongSettings.tint)
            ReadAlongStyle.HIGHLIGHT -> Decoration.Style.Highlight(tint = readAlongSettings.tint)
        }
        val decoration = Decoration(id = "read-along-current", locator = locator, style = style)
        lifecycleScope.launch {
            nav.applyDecorations(listOf(decoration), READ_ALONG_DECORATION_GROUP)
            requestReadAlongDecorationLayout()
        }
    }

    /**
     * Readium lays a decoration out once, when it is added, from the text
     * range's client rects at that moment. Added while the page is still
     * settling (right after the open, or a jump), the item ends up with no
     * boxes and stays invisible: the range is kept, only the layout is empty
     * (seen on the emulator). The decorator re-lays out on request, so ask it
     * again once the page has settled. Harmless when the boxes were drawn.
     */
    private fun requestReadAlongDecorationLayout() {
        val nav = navigator ?: return
        lifecycleScope.launch {
            delay(READ_ALONG_SETTLE_MS)
            nav.evaluateJavascript(
                "(function(){try{readium.getDecorations('$READ_ALONG_DECORATION_GROUP').requestLayout()}catch(e){}})()",
            )
        }
    }

    /** Removes the sentence mark; called when following stops. */
    private fun clearReadAlongDecoration() {
        clearWordMark()
        val nav = navigator ?: return
        lifecycleScope.launch { nav.applyDecorations(emptyList(), READ_ALONG_DECORATION_GROUP) }
    }

    private fun sentenceLocator(point: SyncPointEntity): Locator? {
        val pub = publication ?: return null
        // A sync point's chapter counts every spine item; Readium's reading
        // order leaves out non-linear ones (issue #804). A point in a
        // non-linear item has no page to mark here, so it marks nothing rather
        // than a near-miss in the next chapter.
        val index = chapterNumbering.readingOrderIndexOf(point.epubChapter) ?: return null
        val link = pub.readingOrder.getOrNull(index) ?: return null
        val quote = readAlong.quotes.quoteFor(point) ?: return null
        // The neighbours disambiguate a line the chapter repeats: with the
        // highlight alone Readium settles a tie on the first occurrence, so
        // the mark (and the jump) landed on the wrong copy (issue #793).
        return pub.locatorFromLink(link)?.copy(
            text = Locator.Text(before = quote.before, highlight = quote.highlight, after = quote.after),
        )
    }

    private fun jumpToSentence(point: SyncPointEntity) {
        val locator = sentenceLocator(point) ?: return
        val nav = navigator ?: return
        // Not a user navigation: the collector consults readAlong first and
        // treats the echo as ours (ReadAlongController.onLocatorEmitted).
        programmaticTarget = locator
        if (!nav.go(locator, animated = false)) {
            Log.w(TAG, "read-along: go() declined for chapter ${point.epubChapter}")
        }
        // A jump into another chapter loads a new document, whose word ranges
        // do not exist yet: build them again once the page has settled.
        lifecycleScope.launch {
            delay(READ_ALONG_SETTLE_MS)
            if (readAlong.isFollowing && sameSentencePoint(readAlong.currentPoint, point)) locateWordMark(point)
        }
    }

    /**
     * Whether the sentence's opening words are on the page now. Runs in the
     * current chapter's document, like [extractVisibleTextFromWebView]. Only a
     * text run found inside the current column counts as visible; "hidden"
     * (found on another page) and "missing" (not in this chapter at all) both
     * answer false so the caller jumps — a miss makes Readium decline the
     * jump harmlessly. [where] names the sentence for the log; the text itself
     * is never logged.
     */
    private suspend fun isSentenceVisible(quote: SentenceQuote, where: String = ""): Boolean {
        val nav = navigator ?: return true
        // Picks among repeated copies by their surroundings, like the locator
        // does (issue #793); the script is in SentenceQuote.kt.
        val js = sentenceVisibilityScript(quote.highlight, quote.before, quote.after)
        val answer = nav.evaluateJavascript(js).orEmpty()
        if (answer.contains("missing")) Log.w(TAG, "read-along: sentence not found in page ($where)")
        return answer.contains("visible")
    }

    /**
     * A locator emission while following that was not our own jump's echo
     * (issue #762). Only the page knows whether the user actually left the
     * sentence being read: a settle emission leaves it on screen, a page turn
     * does not. Nothing to compare against before the first audio tick.
     */
    private fun verifySuspectedTurn() {
        // One probe per burst, after the page has settled: a drag that snaps
        // back emits mid-gesture with the columns shifted, and a probe taken
        // then reads the sentence as gone (seen on the emulator).
        suspectVerifyJob?.cancel()
        suspectVerifyJob = lifecycleScope.launch {
            delay(READ_ALONG_SETTLE_MS)
            val point = readAlong.currentPoint ?: return@launch
            val quote = readAlong.quotes.quoteFor(point) ?: return@launch
            val visible = isSentenceVisible(
                quote, "chapter ${point.epubChapter} sentence ${point.epubSentenceIndex}",
            )
            if (readAlong.onSuspectVerified(visible)) {
                showBackToAudio(readAlong.isPaused)
                // Paused: the user is elsewhere, so the word mark goes with the
                // pause. Following again by itself: mark the word again.
                if (readAlong.isPaused) clearWordMark() else locateWordMark(point)
            }
        }
    }

    private fun onBackToAudioTapped() {
        val actions = readAlong.onBackToAudio(System.currentTimeMillis())
        showBackToAudio(false)
        clearWordMark()
        actions.forEach { action ->
            when (action) {
                is ReadAlongController.Action.Decorate -> {
                    applyReadAlongDecoration(action.point)
                    locateWordMark(action.point)
                }
                is ReadAlongController.Action.Jump -> jumpToSentence(action.point)
                is ReadAlongController.Action.Word -> Unit // the next poll tick reports the word
            }
        }
    }

    private fun showBackToAudio(visible: Boolean) {
        backToAudio.visibility = if (visible) View.VISIBLE else View.GONE
    }

    /** The reader's own play/pause: announces a deliberate pause like the player screen does. */
    private fun toggleReadAlongPlayback() {
        val ctrl = readAlongMediaController ?: return
        if (ctrl.playWhenReady) {
            ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_USER_PAUSE, Bundle()), Bundle())
            ctrl.pause()
        } else {
            ctrl.play()
        }
    }

    // ============ Lifecycle ============

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            android.R.id.home -> { saveCurrentPosition(); finish(); true }
            else -> super.onOptionsItemSelected(item)
        }
    }

    override fun onStart() {
        super.onStart()
        // Only a return FROM the background can mean the audiobook was
        // consumed elsewhere while this reader sat unattended (issue #682) —
        // the very first onStart, right after onCreate, has nothing to
        // re-anchor against, and standalone/pair-less opens have no
        // audiobook to have moved on at all. See [hasStoppedSinceOpen].
        if (hasStoppedSinceOpen) {
            hasStoppedSinceOpen = false
            if (!isStandalone && pairId != 0) {
                savesBlockedForReanchor = true
                lifecycleScope.launch {
                    try {
                        reanchorAfterResume()
                    } finally {
                        savesBlockedForReanchor = false
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Retry interceptor install in case the WebView appeared after the initial
        // polling window closed (common for large EPUBs like DCC). No-op if already installed.
        installSelectionInterceptor()
    }

    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        // Received instead of a recreation because the manifest declares
        // android:configChanges (issue #163 — recreation tripped the
        // process-death guard and closed the book). Mark the moment: Readium's
        // re-layout will re-emit a locator shortly, and the collector must
        // treat it as an echo, not a page turn — saving it regressed the
        // anchor to the top of the chapter (found live).
        val now = System.currentTimeMillis()
        // First change of a burst: the view still shows the user's real
        // position — capture it. A rotate-back arriving inside the window
        // must NOT re-capture (the view may be showing the drifted page).
        if (now - lastConfigChangeAtMs > RELAYOUT_ECHO_WINDOW_MS) {
            relayoutRestoreTarget = navigator?.currentLocator?.value
        }
        lastConfigChangeAtMs = now

        // Re-anchor once the re-layout settles: without this the view stays
        // at the chapter top and the exit's onPause save persists the
        // regression (verified live). The go()'s own emission matches
        // programmaticTarget and is swallowed as an echo.
        relayoutRestoreJob?.cancel()
        relayoutRestoreJob = lifecycleScope.launch {
            kotlinx.coroutines.delay(RELAYOUT_RESTORE_DELAY_MS)
            relayoutRestoreTarget?.let { target ->
                programmaticTarget = target
                navigator?.go(target, animated = false)
            }
        }
    }

    override fun onPause() {
        super.onPause()
        saveCurrentPosition()
    }

    override fun onStop() {
        super.onStop()
        hasStoppedSinceOpen = true
    }

    private fun saveCurrentPosition() {
        navigator?.currentLocator?.value?.let { savePosition(it) }
    }

    /**
     * Re-runs the restore ladder if the canonical record has moved on to a
     * new audiobook position since this reader last knew where things stood
     * — issue #682's gap 3, "a reader left open never re-restores".
     *
     * Fetches the record exactly as [onCreate] does at open — the same
     * bounded [prefetchBeforeRestore] pull, falling back to the local
     * bookmark row offline — so a genuine listening session reached through
     * Android Auto, the notification, or another device is seen the same way
     * whether this reader is opened fresh or resumed. [savesBlockedForReanchor]
     * is already set by the caller ([onStart]) before this runs, so nothing
     * saves out from under the decision while it's being made.
     */
    private suspend fun reanchorAfterResume() {
        // The poll resumes with STARTED and jumps to the audio's sentence on
        // its own (issue #762); re-running the ladder would fight it.
        if (readAlong.isFollowing) return
        val pub = publication ?: return
        val nav = navigator ?: return

        val fetch = prefetchBeforeRestore(repository, pairId)
        val fresh = fetch.position?.toStoredPosition()
            ?: repository.getBookmark(pairId)
                ?.toStoredPosition(repository.deviceId)
                .takeIf { !fetch.reachable }

        val reanchor = ResumeReanchorPolicy.shouldReanchor(
            source = fresh?.source,
            recordAudioMs = fresh?.audioPositionMs,
            baselineAudioMs = baselineAudioMs,
        )
        Log.d(TAG, "reanchorAfterResume: shouldReanchor=$reanchor source=${fresh?.source} " +
            "recordAudioMs=${fresh?.audioPositionMs} baselineAudioMs=$baselineAudioMs")
        if (!reanchor) return

        canonicalPosition = fresh
        baselineAudioMs = fresh?.audioPositionMs
        // Runs the same planRestore + ReaderRestoreExecutor chain onCreate's
        // initial restore uses, against the just-refreshed canonicalPosition
        // — including persisting a landed audio rung as this device's hint,
        // same as any other open.
        val target = getInitialLocator(pub)
        if (target != null) {
            // Set BEFORE navigating, same as every other explicit
            // navigator.go(...) call site — see [programmaticTarget] — so the
            // settle emission this produces is recognized as an echo rather
            // than a user page-turn.
            programmaticTarget = target
            nav.go(target, animated = false)
            // Without this, a throttle window that had already elapsed while
            // saves were blocked could autosave the very next locator
            // emission before the settle above is distinguishable from one.
            lastSaveTime = System.currentTimeMillis()
        }
    }

    override fun onDestroy() {
        // Pairs with SyncMapInUse.register in onCreate (issue #678) — the
        // library-refresh prune must stop treating this pair as open the
        // moment the reader actually closes, not merely goes to the
        // background (onCreate/onDestroy, not onPause/onResume, matches how
        // long the reader can plausibly still need the map).
        if (!isStandalone && pairId != 0) SyncMapInUse.unregister(pairId)
        positionSaveJob?.cancel()
        tourNavJob?.cancel()
        readAlongPollJob?.cancel()
        readAlongMediaController?.release()
        readAlongMediaController = null
        // The page counter (issue #730): cancelling the count loads about:blank
        // on the main thread, so the WebView is destroyed after that has run.
        probeJob?.cancel()
        pageCountTriggerJob?.cancel()
        pageCountJob?.cancel()
        pageCountSettingsJob?.cancel()
        pageCounter?.let { counter ->
            pageCounter = null
            Handler(mainLooper).post {
                (counter.parent as? ViewGroup)?.removeView(counter)
                counter.destroy()
            }
        }
        // Every reader View anchor (ReaderPage, ReaderSwitchToAudio, and
        // ReaderProgress since issue #743) is this Activity's alone to publish
        // (issue #597 §2) — clear them so a stale rect from a finished reader
        // never survives into the next screen the tour spotlights. Same
        // reasoning for settled (issue #642): a finished reader must not go
        // on telling the tour the Reader screen is ready.
        tourRegistry.clear(TourAnchor.ReaderPage)
        tourRegistry.clear(TourAnchor.ReaderSwitchToAudio)
        tourRegistry.clear(TourAnchor.ReaderFollowAudio)
        tourRegistry.clear(TourAnchor.ReaderProgress)
        tourRegistry.clearScreen(TourScreen.Reader)
        publication?.close()
        super.onDestroy()
    }
}
