package com.booksync.ui.reader

import android.os.Bundle
import android.util.Log
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
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
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.booksync.R
import com.booksync.data.auth.hasMinRole
import com.booksync.data.local.entity.BookPairEntity
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
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
import androidx.compose.ui.geometry.Rect as ComposeRect
import org.readium.r2.navigator.epub.EpubNavigatorFactory
import org.readium.r2.navigator.epub.EpubNavigatorFragment
import org.readium.r2.navigator.input.InputListener
import org.readium.r2.navigator.input.TapEvent
import org.readium.r2.navigator.preferences.ReadingProgression
import org.readium.r2.shared.ExperimentalReadiumApi
import org.readium.r2.shared.publication.Locator
import org.readium.r2.shared.publication.Publication
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
import javax.inject.Inject

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
    }

    @Inject lateinit var repository: BookSyncRepository
    @Inject lateinit var dictionaryRepository: com.booksync.data.repository.DictionaryRepository
    @Inject lateinit var tokenManager: TokenManager
    @Inject lateinit var networkMonitor: NetworkMonitor

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
    private val chapterTextCache = mutableMapOf<Int, String?>() // spine index → plain text cache
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

    // UI views
    private lateinit var topBar: View
    private lateinit var bottomBar: View
    private lateinit var toolbar: MaterialToolbar
    private lateinit var progressText: TextView
    private lateinit var progressSlider: SeekBar

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
        loadPublication()
    }

    private fun initViews() {
        topBar = findViewById(R.id.top_bar)
        bottomBar = findViewById(R.id.bottom_bar)
        toolbar = findViewById(R.id.toolbar)
        progressText = findViewById(R.id.progress_text)
        progressSlider = findViewById(R.id.progress_slider)



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
        }
        toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
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
        } else {
            tourRegistry.clear(TourAnchor.ReaderSwitchToAudio)
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
     * Publishes the navigator container's window bounds as [TourAnchor.ReaderPage]
     * (issue #597 §2) once it has been laid out, so the walkthrough's first
     * reader step ("tap the middle of the page") and the selection step (which
     * reuses this anchor — see `READER_SELECTION_STEP_ID` in `TourScript.kt`)
     * have a hole to spotlight.
     */
    private fun registerReaderPageAnchor() {
        val container = findViewById<View>(R.id.navigator_container)
        container.post {
            tourRegistry.set(TourAnchor.ReaderPage, container.windowRect())
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

                // A configuration change (rotation, dark-mode toggle, split
                // screen — handled in place since issue #163) re-lays out the
                // WebView, which re-emits a recomputed locator with NO user
                // input — often quantized to the top of the chapter. Adopting
                // it as the new programmatic target and skipping the save
                // keeps the re-layout from regressing the anchor; the user's
                // next real page turn saves normally.
                if (System.currentTimeMillis() - lastConfigChangeAtMs < RELAYOUT_ECHO_WINDOW_MS) {
                    programmaticTarget = locator
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
        val totalProg = locator.locations.totalProgression ?: return
        val pct = (totalProg * 100).toInt()

        // Find chapter title from publication TOC
        val pub = publication ?: return
        val chapterTitle = pub.tableOfContents
            .lastOrNull { toc -> locator.href.toString().contains(toc.href.toString()) }
            ?.title

        val progressStr = if (chapterTitle != null) {
            "$chapterTitle · $pct%"
        } else {
            "$pct%"
        }

        if (!isSeeking) {
            progressText.text = progressStr
            progressSlider.progress = (totalProg * 1000).toInt()
        }
    }

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

    // ============ Bookmark save/restore ============

    private suspend fun getInitialLocator(pub: Publication): Locator? {
        return try {
            val steps = withHandoffAnchor(
                planRestore(
                    canonicalPosition,
                    spineCount = pub.readingOrder.size,
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

    /** Spine index [locator] points at, or -1 if it matches no reading-order item. */
    private fun Publication.spineIndexOf(locator: Locator): Int =
        spineIndexForHref(readingOrder.map { it.href.toString() }, locator.href.toString())

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
        // A standalone ebook has no pair row; everything below keys off
        // isStandalone instead (issue #169).
        if (!isStandalone && pair == null) return
        val pub = publication ?: return
        val chapterIndex = pub.spineIndexOf(locator).coerceAtLeast(0)
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
                epubChapter = chapterIndex,
                epubTextPreview = textPreview,
                epubProgressPercent = bookPercentFor(locator, chapterIndex),
                epubLocator = locatorJson,
            )
            return
        }

        val snapshot = ReaderPositionSnapshot(
            pairId = pairId,
            chapterIndex = chapterIndex,
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

        // Find chapter using robust matching
        val rawChapterIndex = pub.spineIndexOf(locator)
        val chapterIndex = rawChapterIndex.coerceAtLeast(0)

        val progression = locator.locations.progression ?: 0.0
        Log.d(TAG, "syncAudioToPage called! locator.href='${locator.href}', progression=$progression")
        Log.d(TAG, "syncAudioToPage: rawChapterIndex=$rawChapterIndex => chapterIndex=$chapterIndex")

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
                repository.epubToAudioText(pairId, chapterIndex, visible.text)
            } else 0
            // Set before the write, so a savePosition landing in between can't
            // resolve its own sync-point guess over this deliberate match (see
            // ReaderPositionSnapshot.skipSyncPointLookup).
            if (audioMs > 0) sentenceSyncPending = true

            val matched = PageAudioHandoff.apply(
                repository = repository,
                pairId = pairId,
                chapterIndex = chapterIndex,
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

    private fun showDisplaySettings() {
        val nav = navigator ?: return
        displaySettings.showDialog(this, nav, edgeTapSettings)
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
            val chapterIndex = pub.spineIndexOf(locator).coerceAtLeast(0)
            Log.d(TAG, "syncSelectedText: chapterIndex=$chapterIndex, text='${selectedText.take(60)}'")

            val outcome = syncSelectionToAudio(
                repository = repository,
                pairId = pairId,
                chapterIndex = chapterIndex,
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

    private fun formatAudioTime(ms: Long): String {
        val totalSec = (ms / 1000).toInt()
        val h = totalSec / 3600
        val m = (totalSec % 3600) / 60
        val s = totalSec % 60
        return "%d:%02d:%02d".format(h, m, s)
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
        // Both reader view anchors are this Activity's alone to publish
        // (issue #597 §2) — clear them so a stale rect from a finished reader
        // never survives into the next screen the tour spotlights. Same
        // reasoning for settled (issue #642): a finished reader must not go
        // on telling the tour the Reader screen is ready.
        tourRegistry.clear(TourAnchor.ReaderPage)
        tourRegistry.clear(TourAnchor.ReaderSwitchToAudio)
        tourRegistry.clearScreen(TourScreen.Reader)
        publication?.close()
        super.onDestroy()
    }
}
