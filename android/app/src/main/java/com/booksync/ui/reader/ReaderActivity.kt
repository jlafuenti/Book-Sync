package com.booksync.ui.reader

import android.os.Bundle
import android.util.Log
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.SeekBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.booksync.R
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.ReaderPositionSnapshot
import com.booksync.data.repository.toStoredPosition
import com.booksync.data.sync.HINT_READIUM_LOCATOR
import com.booksync.data.sync.StoredPosition
import com.booksync.data.sync.planRestore
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.snackbar.Snackbar
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import org.readium.r2.navigator.epub.EpubNavigatorFactory
import org.readium.r2.navigator.epub.EpubNavigatorFragment
import org.readium.r2.navigator.input.InputListener
import org.readium.r2.navigator.input.TapEvent
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

    private var publication: Publication? = null
    private var navigator: EpubNavigatorFragment? = null
    private var pair: BookPairEntity? = null
    private var pairId: Int = 0

    /**
     * Standalone mode (issue #169): non-zero when opened on an unpaired ebook.
     * [isStandalone] is the single switch every pair-dependent branch reads, so
     * that "does this book have audio" is asked once rather than inferred from
     * a null pair in a dozen places.
     */
    private var ebookId: Int = 0
    private var standaloneEbook: com.booksync.data.local.entity.EBookEntity? = null
    private val isStandalone: Boolean get() = ebookId != 0
    private var positionSaveJob: Job? = null
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

        displaySettings.load()
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
                R.id.action_switch_audio -> { syncAudioToPage(); true }
                R.id.action_font_settings -> { showDisplaySettings(); true }
                else -> false
            }
        }

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
            toggleBars()
            return true
        }
    }

    private fun toggleBars() {
        setBarsVisible(!isBarVisible)
    }

    private fun setBarsVisible(visible: Boolean) {
        isBarVisible = visible
        val visibility = if (visible) View.VISIBLE else View.GONE
        topBar.visibility = visibility
        bottomBar.visibility = visibility
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

        positionSaveJob?.cancel()
        positionSaveJob = lifecycleScope.launch {
            // Start the throttle window at "now" rather than 0 so the first
            // locator the navigator emits — which is just the position we
            // restored — isn't written straight back to the server. Echoing it
            // is pointless when the restore worked, and destructive when it
            // didn't: a restore that lands on page one would otherwise
            // overwrite a real position from another device with chapter 0.
            var lastSaveTime = System.currentTimeMillis()
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
                handoffAudioMs = intent.getLongExtra(EXTRA_HANDOFF_AUDIO_MS, 0L).toInt(),
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

        // Inject selection tracker on every page turn (content may have changed).
        // Called on the main thread before the coroutine, so WebView access is safe.
        injectSelectionTracker()

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
        repository.saveReaderPosition(snapshot)
    }

    // ============ Manual Sync ============

    /**
     * Gets the visible paragraph text from the correct Readium iframe for [chapterHref].
     * Readium pre-renders adjacent chapters in background iframes so we MUST target
     * the iframe whose src URL matches the current chapter filename.
     * Within that iframe, Readium CSS uses horizontal CSS columns for pagination;
     * the fragments of the current page sit in [0, innerWidth).
     *
     * Reports *how* it answered (issue #131). The scroll-position fallback is an
     * estimate by character count, which does not track page position under
     * column pagination — the caller must not treat it as a real read.
     */
    private suspend fun extractVisibleTextFromWebView(chapterHref: String): VisibleText =
        suspendCancellableCoroutine { cont ->
            val webView = navigator?.view?.let { findWebView(it) }
            if (webView == null) {
                cont.resume(VisibleText.EMPTY)
                return@suspendCancellableCoroutine
            }
            val chapterFile = chapterHref.substringAfterLast("/").ifEmpty { chapterHref }
            val quotedFile = org.json.JSONObject.quote(chapterFile)
            val js = """
                (function() {
                    var chapterFile = $quotedFile;

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

                    // Target the iframe for the current chapter
                    var frames = document.querySelectorAll('iframe');
                    var targetDoc = null;
                    for (var i = 0; i < frames.length; i++) {
                        try {
                            if (frames[i].src.indexOf(chapterFile) >= 0) {
                                targetDoc = frames[i].contentDocument;
                                break;
                            }
                        } catch(e) {}
                    }

                    function result(text, source) {
                        return JSON.stringify({ text: text, source: source });
                    }

                    if (targetDoc) {
                        var text = getVisibleText(targetDoc);
                        if (text.trim().length > 10) return result(text, 'dom');
                        var guess = scrollBasedText(targetDoc);
                        if (guess.trim().length > 10) return result(guess, 'estimated');
                    }

                    // No matching iframe — try every other frame first.
                    for (var i = 0; i < frames.length; i++) {
                        try {
                            var text = getVisibleText(frames[i].contentDocument);
                            if (text.trim().length > 10) return result(text, 'dom');
                        } catch(e) {}
                    }

                    // Then this WebView's own document. The comment above used
                    // to promise this case ("chapter may load directly in
                    // WebView") while the loop it introduced searched `frames`
                    // again, so a build that renders the chapter directly —
                    // with no iframes at all — always fell through to 'none'.
                    // Every "Switch to Audio" then handed over on the chapter
                    // anchor instead of the sentence on screen, which is the
                    // whole of what issue #114 added.
                    var own = getVisibleText(document);
                    if (own.trim().length > 10) return result(own, 'dom');
                    var ownGuess = scrollBasedText(document);
                    if (ownGuess.trim().length > 10) return result(ownGuess, 'estimated');

                    return result('', 'none');
                })()
            """.trimIndent()
            // The bridge double-encodes our JSON.stringify(...); VisibleText.parse
            // handles both shapes, so the whole decode is one unit-tested step.
            webView.evaluateJavascript(js) { result -> cont.resume(VisibleText.parse(result)) }
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

        val chapterHref = locator.href.toString()
        lifecycleScope.launch {
            // Only a real DOM read of the current column is worth seeking on.
            // The extractor's other answer is a character-offset estimate, which
            // under column pagination routinely names text from another page —
            // and a confident seek to the wrong second is worse than the anchor
            // handoff the user would otherwise have got (issue #131). Pass the
            // chapter href so we target the right iframe; Readium pre-loads the
            // adjacent chapters.
            val visible = extractVisibleTextFromWebView(chapterHref)
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

    private fun showDisplaySettings() {
        val nav = navigator ?: return
        displaySettings.showDialog(this, nav)
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
                override fun webView() = navigator?.view?.let { findWebView(it) }
                override val syncToAudioAvailable: Boolean get() = pair?.audiobookDownloaded == true
                override fun onDefine(selectedText: String) = defineSelectedWord(selectedText)
                override fun onSyncToAudio(selectedText: String) = syncSelectedTextToAudio(selectedText)
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
     * Look up the first word of the user's selection on dictionaryapi.dev
     * and present the result in a dialog. Silent/Toast on offline or 404.
     */
    private fun defineSelectedWord(selectedText: String) {
        val firstToken = firstDefinableToken(selectedText)

        if (firstToken.isEmpty()) {
            android.widget.Toast.makeText(this, "Select a word to define", android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        Log.d(TAG, "Defining word: '$firstToken'")

        lifecycleScope.launch {
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

    private fun syncSelectedTextToAudio(rawSelectedText: String) {
        // Use the text captured when the ActionMode started — by the time this
        // click fires, the ActionMode interaction has already cleared
        // window.getSelection().
        val selectedText = rawSelectedText.trim()
        Log.d(TAG, "Selected text: '${selectedText.take(100)}'")

        if (selectionTooShortToSync(selectedText)) {
            android.widget.Toast.makeText(this, "Select more text to sync", android.widget.Toast.LENGTH_SHORT).show()
            return
        }

        val locator = navigator?.currentLocator?.value ?: return
        val pub = publication ?: return
        val chapterIndex = pub.spineIndexOf(locator).coerceAtLeast(0)
        Log.d(TAG, "syncSelectedText: chapterIndex=$chapterIndex, text='${selectedText.take(60)}'")

        lifecycleScope.launch {
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

    /** Injects the selection-change tracker into the Readium WebView (idempotent). */
    private fun injectSelectionTracker() = selectionController.injectTracker()

    // ============ Lifecycle ============

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            android.R.id.home -> { saveCurrentPosition(); finish(); true }
            else -> super.onOptionsItemSelected(item)
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

    private fun saveCurrentPosition() {
        navigator?.currentLocator?.value?.let { savePosition(it) }
    }

    override fun onDestroy() {
        positionSaveJob?.cancel()
        publication?.close()
        super.onDestroy()
    }
}
