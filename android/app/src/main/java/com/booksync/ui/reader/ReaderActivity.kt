package com.booksync.ui.reader

import android.os.Bundle
import android.util.Log
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.SeekBar
import android.widget.TextView
import org.readium.r2.navigator.preferences.Theme
import org.readium.r2.navigator.preferences.FontFamily
import androidx.appcompat.app.AppCompatActivity
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.booksync.R
import com.booksync.SyncState
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.ReaderPositionSnapshot
import com.booksync.data.repository.toStoredPosition
import com.booksync.data.sync.HINT_READIUM_LOCATOR
import com.booksync.data.sync.RestoreStep
import com.booksync.data.sync.StoredPosition
import com.booksync.data.sync.planRestore
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
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
import org.readium.r2.navigator.epub.EpubPreferences
import org.readium.r2.navigator.input.InputListener
import org.readium.r2.navigator.input.TapEvent
import org.readium.r2.shared.ExperimentalReadiumApi
import org.readium.r2.shared.publication.Locator
import org.readium.r2.shared.publication.Publication
import org.readium.r2.shared.util.AbsoluteUrl
import org.readium.r2.shared.util.asset.AssetRetriever
import org.readium.r2.shared.util.http.DefaultHttpClient
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
        const val RESULT_SWITCH_TO_AUDIO = 42
        private const val TAG = "ReaderActivity"
        private const val NAV_FRAGMENT_TAG = "EpubNavigatorFragment"
        private const val SAVE_INTERVAL_MS = 5000L
        /** If audio moved less than this since the locator was captured, reuse it verbatim. */
        private const val LOCATOR_REUSE_THRESHOLD_MS = 30_000
        private const val PREFS_NAME = "reader_display"
        private const val KEY_FONT_SIZE = "font_size"
        private const val KEY_THEME = "theme"
        private const val KEY_FONT_FAMILY = "font_family"
        private const val KEY_LINE_SPACING = "line_spacing"
        private const val KEY_MARGINS = "margins"

        /**
         * Injected into the Readium host WebView on every page turn.
         * Listens for text selection in all epub iframes and stores the last selection in
         * window.top._bookSyncSelection so it survives ActionMode dismissal.
         * Readium serves epub content from localhost iframes, so same-origin access works.
         */
        private const val SELECTION_TRACKER_JS = """
            (function() {
                function installInDoc(doc) {
                    if (!doc || doc._bsListenerAdded) return;
                    doc._bsListenerAdded = true;
                    doc.addEventListener('selectionchange', function() {
                        try {
                            var sel = doc.defaultView.getSelection();
                            var text = sel ? sel.toString().trim() : '';
                            if (text.length > 3) { window.top._bookSyncSelection = text; }
                        } catch(e) {}
                    });
                }
                installInDoc(document);
                var frames = document.querySelectorAll('iframe');
                for (var i = 0; i < frames.length; i++) {
                    try {
                        installInDoc(frames[i].contentDocument);
                        frames[i].addEventListener('load', (function(f) {
                            return function() { try { installInDoc(f.contentDocument); } catch(e) {} };
                        })(frames[i]));
                    } catch(e) {}
                }
                new MutationObserver(function(ms) {
                    ms.forEach(function(m) {
                        m.addedNodes.forEach(function(n) {
                            if (n.nodeName === 'IFRAME') {
                                n.addEventListener('load', function() {
                                    try { installInDoc(n.contentDocument); } catch(e) {}
                                });
                            }
                        });
                    });
                }).observe(document.body || document, {childList: true, subtree: true});
            })()
        """
    }

    @Inject lateinit var repository: BookSyncRepository
    @Inject lateinit var dictionaryRepository: com.booksync.data.repository.DictionaryRepository

    private var publication: Publication? = null
    private var navigator: EpubNavigatorFragment? = null
    private var pair: BookPairEntity? = null
    private var pairId: Int = 0
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

    /**
     * The navigator's very first locator emission after (re)creation reflects
     * the restore (the initial locator passed to the fragment factory), not a
     * user page-turn — it must not be read as navigation. Cleared after that
     * first emission; every locator change after it is the user turning a
     * page (or dragging the progress slider), so it's reported to
     * [savePolicy] — see `startPositionTracking`.
     */
    private var awaitingRestoreLocator = true

    // Holds the user's selected text captured in onActionModeStarted, before ActionMode clears it
    private var lastSelectedText: String = ""

    // UI views
    private lateinit var topBar: View
    private lateinit var bottomBar: View
    private lateinit var toolbar: MaterialToolbar
    private lateinit var progressText: TextView
    private lateinit var progressSlider: SeekBar

    override fun onCreate(savedInstanceState: Bundle?) {
        pairId = intent.getIntExtra(EXTRA_PAIR_ID, 0)
        Log.d(TAG, "onCreate pairId=$pairId savedState=${savedInstanceState != null}")

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

        loadSavedPreferences()
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
        toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_switch_audio -> { switchToAudio(); true }
                R.id.action_font_settings -> { showFontSettings(); true }
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

    private fun loadPublication() {
        lifecycleScope.launch {
            try {
                pair = repository.getPairById(pairId)
                val bookPair = pair ?: run {
                    Log.e(TAG, "Book pair not found for pairId=$pairId")
                    finish()
                    return@launch
                }
                Log.d(TAG, "Book pair: ${bookPair.ebookTitle}")

                toolbar.title = bookPair.ebookTitle

                val ebookFile = repository.getEbookFile(bookPair)
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

                val pub = PublicationOpener(parser)
                    .open(asset, allowUserInteraction = false)
                    .getOrNull()
                    ?: run { Log.e(TAG, "Failed to open publication"); finish(); return@launch }

                Log.d(TAG, "Publication opened: ${pub.metadata.title}, readingOrder=${pub.readingOrder.size} items")
                publication = pub

                // Pull the server's position before restoring (issue #40). The
                // reader used to read only the local cache, so a position set
                // on another device was never seen and the phone reopened at
                // its own last page — the locator checks below can't catch
                // that, since a stale local bookmark agrees with itself.
                // Offline-safe and unsynced-local-safe: refreshBookmark keeps
                // the local row in both cases.
                repository.refreshBookmark(pairId)

                // Fetch the canonical record before restoring. Reading only the
                // local cache meant a position set on another device was never
                // seen, so the reader confidently reopened at its own old page.
                val fetch = repository.fetchPosition("pair", pairId)
                canonicalPosition = fetch.position?.toStoredPosition()
                    ?: repository.getBookmark(pairId)
                        ?.toStoredPosition(repository.deviceId)
                        .takeIf { !fetch.reachable }

                val initialLocator = getInitialLocator(pub)
                Log.d(TAG, "Initial locator: $initialLocator")

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
                applyPreferences()

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

    /** Find which spine index contains the given text preview.
     *  Searches outward from hintIdx to prefer nearby matches. */
    private suspend fun findSpineIndexForText(previewText: String, hintIdx: Int = -1): Int? {
        val pub = publication ?: return null
        if (previewText.isEmpty()) return null
        
        // Strip leading chapter headings (e.g. "CHAPTER 28\n") since epub text has different formatting
        val stripped = previewText.replace(Regex("^(CHAPTER\\s+\\d+|PROLOGUE)[\\s\\n,.]*", RegexOption.IGNORE_CASE), "")
        // Normalize newlines to spaces and collapse whitespace
        val searchText = stripped.replace("\n", " ").replace(Regex("\\s+"), " ").take(60).trim()
        
        if (searchText.length < 10) {
            Log.d(TAG, "findSpineIndexForText: search text too short after cleaning: '$searchText'")
            return null
        }
        
        val n = pub.readingOrder.size
        // If we have a valid hint, search outward from it
        val hint = if (hintIdx in 0 until n) hintIdx else n / 2
        for (offset in 0 until n) {
            for (candidate in listOf(hint + offset, hint - offset).distinct()) {
                if (candidate in 0 until n) {
                    val plainText = getChapterPlainText(candidate) ?: continue
                    if (plainText.contains(searchText, ignoreCase = true)) {
                        Log.d(TAG, "findSpineIndexForText: found '${searchText.take(40)}' at spine $candidate (hint=$hint)")
                        return candidate
                    }
                }
            }
        }
        Log.d(TAG, "findSpineIndexForText: no match for '${searchText.take(40)}'")
        return null
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

    private suspend fun findTextProgressionInChapter(chapterIndex: Int, textPreview: String): Double? {
        val plainText = getChapterPlainText(chapterIndex) ?: return null
        // Strip chapter heading and normalize whitespace
        val stripped = textPreview.replace(Regex("^(CHAPTER\\s+\\d+|PROLOGUE)[\\s\\n,.]*", RegexOption.IGNORE_CASE), "")
        val cleanPreview = stripped.replace("\n", " ").replace(Regex("\\s+"), " ").trim().lowercase()
        val cleanPlain = plainText.replace(Regex("\\s+"), " ").lowercase()
        
        val index = cleanPlain.indexOf(cleanPreview)
        if (index >= 0) {
            return index.toDouble() / cleanPlain.length
        }
        
        // Approximate fallback if exact search misses (typos etc)
        // Just look for the first 20 characters
        val shortPreview = cleanPreview.take(20)
        val shortIndex = cleanPlain.indexOf(shortPreview)
        if (shortIndex >= 0) {
            return shortIndex.toDouble() / cleanPlain.length
        }
        
        return null
    }

    // ============ Bar toggle ============

    private val tapListener = object : InputListener {
        override fun onTap(event: TapEvent): Boolean {
            toggleBars()
            return true
        }
    }

    private fun toggleBars() {
        isBarVisible = !isBarVisible
        val visibility = if (isBarVisible) View.VISIBLE else View.GONE
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
                // Update progress UI
                updateProgressUI(locator)

                // The first emission is the restore settling in, not a user
                // action — everything after it is the user turning a page or
                // dragging the slider, and unblocks a full save even when the
                // restore itself never resolved (see PositionSavePolicy).
                if (awaitingRestoreLocator) {
                    awaitingRestoreLocator = false
                } else {
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
            // Get content-weighted chapter lengths (precomputed in background by startPositionTracking).
            // Falls back to computing now if somehow not ready yet.
            val lengths = chapterLengthsDeferred?.await()
                ?: LongArray(readingOrder.size) { i -> getChapterPlainText(i)?.length?.toLong() ?: 1000L }

            val totalLength = lengths.sum().coerceAtLeast(1)
            val targetChar = (progress * totalLength).toLong().coerceIn(0, totalLength - 1)

            // Find which spine item contains targetChar
            var accumulated = 0L
            var targetSpineIndex = readingOrder.size - 1
            var targetProgression = 1.0
            for (i in lengths.indices) {
                val len = lengths[i]
                if (accumulated + len > targetChar) {
                    targetSpineIndex = i
                    targetProgression = if (len > 0) (targetChar - accumulated).toDouble() / len else 0.0
                    break
                }
                accumulated += len
            }

            Log.d(TAG, "goToProgress: ${(progress * 100).toInt()}%% -> spine=$targetSpineIndex, intraProgression=%.3f".format(targetProgression))
            val link = readingOrder[targetSpineIndex]
            val locator = pub.locatorFromLink(link) ?: return@launch
            nav.go(
                locator.copy(locations = locator.locations.copy(
                    progression = targetProgression.coerceIn(0.0, 1.0)
                )),
                animated = false
            )
        }
    }

    // ============ Bookmark save/restore ============

    private suspend fun getInitialLocator(pub: Publication): Locator? {
        return try {
            val position = canonicalPosition
            val steps = planRestore(
                position,
                spineCount = pub.readingOrder.size,
                deviceId = repository.deviceId,
                hintKind = HINT_READIUM_LOCATOR,
            )
            Log.d(TAG, "getInitialLocator: plan=${steps.map { it.kind }}")

            for (step in steps) {
                val locator = executeRestoreStep(pub, step, position)
                if (locator != null) {
                    Log.d(TAG, "getInitialLocator: restored via '${step.kind}'")
                    // Monotonic — see PositionSavePolicy. An exception thrown
                    // by a LATER step (caught below) can no longer demote this
                    // back to Unresolved, unlike the old boolean flag.
                    savePolicy.onRestoreOutcome(PositionSavePolicy.RestoreOutcome.Landed)
                    return locator
                }
                Log.d(TAG, "getInitialLocator: step '${step.kind}' did not resolve")
            }

            // No steps at all means the book is genuinely unread, and opening
            // at the beginning is correct — a full save is safe. Steps that
            // all failed mean we hold a position we could not resolve — the
            // policy still allows a local metadata-only stamp (never the full
            // anchors) until the user actually turns a page.
            val outcome = if (steps.isEmpty())
                PositionSavePolicy.RestoreOutcome.Unread
            else
                PositionSavePolicy.RestoreOutcome.Unresolved
            savePolicy.onRestoreOutcome(outcome)
            if (outcome == PositionSavePolicy.RestoreOutcome.Unresolved) {
                Log.w(TAG, "getInitialLocator: position unresolved — full saves withheld until a page turn")
            }
            null
        } catch (e: Exception) {
            Log.w(TAG, "Error getting initial locator", e)
            // Monotonic — a landed rung earlier in the ladder is not undone
            // by an exception thrown while trying a later one.
            savePolicy.onRestoreOutcome(PositionSavePolicy.RestoreOutcome.Unresolved)
            null
        }
    }

    /** Try one rung of the ladder. Returns null when it doesn't resolve. */
    private suspend fun executeRestoreStep(
        pub: Publication,
        step: RestoreStep,
        position: StoredPosition?,
    ): Locator? = when (step) {
        is RestoreStep.Hint ->
            runCatching { Locator.fromJSON(org.json.JSONObject(step.value)) }.getOrNull()

        is RestoreStep.Text -> {
            val seed = step.seedChapter ?: 0
            val idx = findSpineIndexForText(step.text, seed)
            if (idx != null && idx in pub.readingOrder.indices) {
                pub.locatorFromLink(pub.readingOrder[idx])?.copy(
                    locations = Locator.Locations(
                        progression = findTextProgressionInChapter(idx, step.text) ?: 0.0
                    )
                )
            } else null
        }

        is RestoreStep.Chapter ->
            pub.readingOrder.getOrNull(step.chapter)?.let { pub.locatorFromLink(it) }

        is RestoreStep.Percent -> locatorForProgress(pub, step.percent / 100.0)

        is RestoreStep.Audio -> {
            // Audio -> sync map -> preview -> the same text search as above.
            val (syncChapter, previewText) = repository.audioToEpubText(
                pairId, step.audioPositionMs)
            val idx = findSpineIndexForText(previewText, syncChapter)
                ?: syncChapter.takeIf { it in pub.readingOrder.indices }
            if (idx != null && idx in pub.readingOrder.indices && previewText.isNotEmpty()) {
                val computed = pub.locatorFromLink(pub.readingOrder[idx])?.copy(
                    locations = Locator.Locations(
                        progression = findTextProgressionInChapter(idx, previewText) ?: 0.0
                    )
                )
                // Persist so the next open at this audio position takes the
                // exact-hint rung instead of redoing this lossy chain.
                if (computed != null) {
                    repository.updateBookmarkLocator(
                        pairId, computed.toJSON().toString(), step.audioPositionMs)
                }
                computed
            } else null
        }
    }

    /** Map a 0..1 book fraction onto a locator, weighting chapters by length. */
    private suspend fun locatorForProgress(pub: Publication, progress: Double): Locator? {
        val readingOrder = pub.readingOrder
        if (readingOrder.isEmpty()) return null
        val lengths = chapterLengthsDeferred?.await()
            ?: LongArray(readingOrder.size) { i -> getChapterPlainText(i)?.length?.toLong() ?: 1000L }
        val total = lengths.sum().coerceAtLeast(1)
        val targetChar = (progress * total).toLong().coerceIn(0, total - 1)

        var accumulated = 0L
        var spineIndex = readingOrder.size - 1
        var withinChapter = 1.0
        for (i in lengths.indices) {
            val len = lengths[i]
            if (accumulated + len > targetChar) {
                spineIndex = i
                withinChapter = if (len > 0) (targetChar - accumulated).toDouble() / len else 0.0
                break
            }
            accumulated += len
        }
        return pub.locatorFromLink(readingOrder[spineIndex])?.copy(
            locations = Locator.Locations(progression = withinChapter.coerceIn(0.0, 1.0))
        )
    }

    /**
     * Resolve a position from the portable anchor alone (chapter + sentence),
     * used when the stored locator can't be trusted. Same preview -> spine ->
     * progression chain the audiobook slow path uses; falls back to the start
     * of the chapter when there's no usable preview text.
     */
    private suspend fun locatorFromChapterAnchor(pub: Publication, chapter: Int?): Locator? {
        val bookmark = repository.getBookmark(pairId)
        val chapterIdx = chapter?.takeIf { it in pub.readingOrder.indices } ?: return null
        val previewText = repository.epubTextForSentence(
            pairId, chapterIdx, bookmark?.epubSentenceIndex)
        val base = pub.locatorFromLink(pub.readingOrder[chapterIdx]) ?: return null
        if (previewText.isEmpty()) return base
        val resolvedIdx = findSpineIndexForText(previewText, chapterIdx) ?: chapterIdx
        val link = pub.readingOrder.getOrNull(resolvedIdx) ?: return base
        val resolvedBase = pub.locatorFromLink(link) ?: return base
        val progressionVal = findTextProgressionInChapter(resolvedIdx, previewText) ?: 0.0
        return resolvedBase.copy(locations = Locator.Locations(progression = progressionVal))
    }

    /** Extract a text preview from a chapter at a given progression, stripping headings and book title. */
    private suspend fun extractTextPreview(chapterIndex: Int, progression: Double): String {
        val plainText = getChapterPlainText(chapterIndex) ?: return ""
        val charIndex = (plainText.length * progression).toInt()
        val startIndex = maxOf(0, charIndex - 20)
        val endIndex = minOf(charIndex + 200, plainText.length)
        // Extract a focused window, strip chapter headings
        // Handle "CHAPTER N, Title CHAPTER N" pattern (Jsoup has no newlines)
        var text = plainText.substring(startIndex, endIndex)
            .replace(Regex("(?i)^chapter\\s+\\d+.{0,120}?chapter\\s+\\d+\\s*"), "")
            .replace(Regex("(?i)^chapter\\s+\\d+[,.]?\\s*"), "")
            .replace(Regex("(?i)^prologue[,.]?\\s*"), "")
            .trim()
        // Strip book title from start (Jsoup includes <title> text at top of chapter)
        val bookTitle = pair?.ebookTitle
        if (!bookTitle.isNullOrEmpty()) {
            val titlePattern = Regex("^${Regex.escape(bookTitle)}\\s*", RegexOption.IGNORE_CASE)
            text = titlePattern.replace(text, "") // Remove first occurrence
            text = titlePattern.replace(text, "") // Remove possible second occurrence
            text = text.trim()
        }
        return text
    }

    /** Spine index [locator] points at, or -1 if it matches no reading-order item. */
    private fun Publication.spineIndexOf(locator: Locator): Int =
        spineIndexForHref(readingOrder.map { it.href.toString() }, locator.href.toString())

    /**
     * Book-level progress (0-100) for the UserProgress record, matching the
     * scale the web reader writes. Uses Readium's totalProgression when the
     * publication provides one, else the cached chapter lengths.
     */
    private suspend fun bookPercentFor(locator: Locator, chapterIndex: Int): Float? =
        bookProgressPercent(
            totalProgression = locator.locations.totalProgression,
            chapterLengths = chapterLengthsDeferred?.await() ?: LongArray(0),
            spineIndex = chapterIndex,
            chapterProgression = locator.locations.progression ?: 0.0,
        )

    private fun savePosition(locator: Locator) {
        pair ?: return
        val verdict = savePolicy.verdictForSave()
        Log.d(TAG, "savePosition: verdict=$verdict")

        if (verdict == PositionSavePolicy.SaveVerdict.LocalMetadataOnly) {
            // The restore hasn't resolved yet, so this locator is the ladder's
            // failed guess (spine 0), not a place the user chose. Only the
            // bookmark's source/updatedAt are stamped — see
            // BookSyncRepository.updateBookmarkMetadata — so format routing
            // (resolvePairOpenTarget) still works without risking the
            // chapter-0 data loss a full save here would recreate.
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

        // Inject selection tracker on every page turn (content may have changed).
        // Called on the main thread before the coroutine, so WebView access is safe.
        injectSelectionTracker()
        lifecycleScope.launch {
            try {
                val pub = publication ?: return@launch
                val chapterIndex = pub.spineIndexOf(locator).coerceAtLeast(0)
                val progression = locator.locations.progression ?: 0.0
                val textPreview = extractTextPreview(chapterIndex, progression)
                val locatorJson = locator.toJSON().toString()

                // A sync-map match upgrades the anchor to a sentence and its
                // audio position. A miss is not a failure: the chapter and the
                // preview still describe where the reader is, and the other
                // clients can resolve from them. Resolving this needs only a
                // DB read (no publication/navigator access), so — like
                // everything else here — it happens before handoff.
                val syncPoint = if (sentenceSyncPending) null
                    else repository.getSyncPointForEpubText(pairId, chapterIndex, textPreview)

                // Snapshot everything the save needs, then hand off. The save
                // itself runs on the repository's app-scoped coroutine (see
                // saveReaderPosition) so activity teardown — this call is
                // itself running inside lifecycleScope — can't cancel it
                // mid-write; the snapshot exists so that detached coroutine
                // never has to touch the publication, navigator, or WebView.
                val snapshot = ReaderPositionSnapshot(
                    pairId = pairId,
                    chapterIndex = syncPoint?.epubChapter ?: chapterIndex,
                    epubSentenceIndex = syncPoint?.epubSentenceIndex,
                    locatorJson = locatorJson,
                    textPreview = textPreview,
                    progressPercent = bookPercentFor(locator, chapterIndex),
                    audioPositionMs = syncPoint?.audioStartMs,
                    capturedAtMillis = System.currentTimeMillis(),
                )
                repository.saveReaderPosition(snapshot)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Error saving position", e)
            }
        }
    }

    // ============ Manual Sync ============

    /**
     * Gets the visible paragraph text from the correct Readium iframe for [chapterHref].
     * Readium pre-renders adjacent chapters in background iframes so we MUST target
     * the iframe whose src URL matches the current chapter filename.
     * Within that iframe, Readium CSS uses horizontal CSS columns for pagination;
     * elements on the current page have BoundingClientRect.left in [0, innerWidth).
     * Falls back to scroll-position text extraction if BoundingClientRect gives nothing.
     */
    private suspend fun extractVisibleTextFromWebView(chapterHref: String): String =
        suspendCancellableCoroutine { cont ->
            val webView = navigator?.view?.let { findWebView(it) }
            if (webView == null) {
                cont.resume("")
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
                        var vpH = win ? win.innerHeight : 1200;
                        var elems = doc.querySelectorAll('p, li, blockquote');
                        for (var i = 0; i < elems.length; i++) {
                            var el = elems[i];
                            if (el.children.length > 4) continue;
                            var r = el.getBoundingClientRect();
                            // Readium paginated = CSS columns with horizontal scroll:
                            // current page elements have left in [0, vpW), bottom > 0
                            if (r.width > 0 && r.height > 0 &&
                                r.right > 0 && r.left < vpW &&
                                r.bottom > 0 && r.top < vpH) {
                                var text = (el.innerText || el.textContent || '').trim();
                                if (text.length > 20) return text.substring(0, 350);
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

                    if (targetDoc) {
                        var text = getVisibleText(targetDoc);
                        if (!text) text = scrollBasedText(targetDoc);
                        if (text.trim().length > 10) return JSON.stringify(text);
                    }

                    // No matching iframe — try all frames (chapter may load directly in WebView)
                    for (var i = 0; i < frames.length; i++) {
                        try {
                            var text = getVisibleText(frames[i].contentDocument);
                            if (text.trim().length > 10) return JSON.stringify(text);
                        } catch(e) {}
                    }
                    return JSON.stringify('');
                })()
            """.trimIndent()
            webView.evaluateJavascript(js) { result ->
                val text = try {
                    org.json.JSONArray("[$result]").getString(0)
                } catch (e: Exception) {
                    result?.trim('"') ?: ""
                }
                cont.resume(text.replace("\\n", " ").replace("\\t", " ").trim())
            }
        }

    private fun syncAudioToPage() {
        if (pair?.audiobookDownloaded != true) {
            android.widget.Toast.makeText(this, "Audiobook not downloaded", android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        val locator = navigator?.currentLocator?.value ?: return
        val pub = publication ?: return
        
        // Find chapter using robust matching
        val rawChapterIndex = pub.spineIndexOf(locator)
        val chapterIndex = rawChapterIndex.coerceAtLeast(0)

        val progression = locator.locations.progression ?: 0.0
        Log.d(TAG, "syncAudioToPage called! locator.href='${locator.href}', progression=$progression")
        Log.d(TAG, "syncAudioToPage: rawChapterIndex=$rawChapterIndex => chapterIndex=$chapterIndex")

        val chapterHref = locator.href.toString()
        lifecycleScope.launch {
            // Prefer DOM-based visible text (accurate) over progression * length (unreliable,
            // because Readium pagination splits by pixel height, not character count).
            // Pass chapter href so we target the correct iframe (Readium pre-loads adjacent chapters).
            val domText = extractVisibleTextFromWebView(chapterHref)
            val textPreview = if (domText.length >= 10) domText
                              else extractTextPreview(chapterIndex, progression)
            Log.d(TAG, "syncAudioToPage: textPreview='${textPreview.take(80)}'")

            val audioMs = repository.epubToAudioText(pairId, chapterIndex, textPreview, rewindMs = 2000)
            if (audioMs > 0) {
                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = chapterIndex,
                    audioPositionMs = audioMs,
                )
                android.widget.Toast.makeText(this@ReaderActivity, "Audio synced — switching to player", android.widget.Toast.LENGTH_SHORT).show()
                switchToAudio()
            } else {
                android.widget.Toast.makeText(this@ReaderActivity, "No matching audio found for this page", android.widget.Toast.LENGTH_SHORT).show()
            }
        }
    }

    // ============ Display Settings ============

    // Track cumulative preferences so changes don't wipe each other
    private var currentPreferences = EpubPreferences()

    private fun loadSavedPreferences() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val fontSize = prefs.getFloat(KEY_FONT_SIZE, 1.0f).toDouble()
        val themeName = prefs.getString(KEY_THEME, null)
        val theme = when (themeName) {
            "light" -> Theme.LIGHT
            "sepia" -> Theme.SEPIA
            "dark" -> Theme.DARK
            else -> null
        }
        val fontFamilyName = prefs.getString(KEY_FONT_FAMILY, null)
        val fontFamily = when (fontFamilyName) {
            "serif" -> FontFamily.SERIF
            "sans-serif" -> FontFamily.SANS_SERIF
            "cursive" -> FontFamily.CURSIVE
            "monospace" -> FontFamily.MONOSPACE
            "system" -> null // Default
            else -> null
        }
        val lineSpacingRaw = prefs.getFloat(KEY_LINE_SPACING, -1f)
        val lineSpacing = if (lineSpacingRaw > 0) lineSpacingRaw.toDouble() else null
        
        val marginsRaw = prefs.getFloat(KEY_MARGINS, -1f)
        val margins = if (marginsRaw > 0) marginsRaw.toDouble() else null

        currentPreferences = EpubPreferences(
            fontSize = fontSize,
            theme = theme,
            fontFamily = fontFamily,
            lineHeight = lineSpacing,
            pageMargins = margins,
            publisherStyles = false
        )
    }

    private fun savePreferences() {
        val editor = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
        editor.putFloat(KEY_FONT_SIZE, (currentPreferences.fontSize ?: 1.0).toFloat())
        
        val themeName = when (currentPreferences.theme) {
            Theme.LIGHT -> "light"
            Theme.SEPIA -> "sepia"
            Theme.DARK -> "dark"
            else -> null
        }
        if (themeName != null) editor.putString(KEY_THEME, themeName)
        else editor.remove(KEY_THEME)

        val fontFamilyName = when (currentPreferences.fontFamily) {
            FontFamily.SERIF -> "serif"
            FontFamily.SANS_SERIF -> "sans-serif"
            FontFamily.CURSIVE -> "cursive"
            FontFamily.MONOSPACE -> "monospace"
            else -> null
        }
        if (fontFamilyName != null) editor.putString(KEY_FONT_FAMILY, fontFamilyName)
        else editor.remove(KEY_FONT_FAMILY)

        if (currentPreferences.lineHeight != null) {
            editor.putFloat(KEY_LINE_SPACING, currentPreferences.lineHeight!!.toFloat())
        } else {
            editor.remove(KEY_LINE_SPACING)
        }

        if (currentPreferences.pageMargins != null) {
            editor.putFloat(KEY_MARGINS, currentPreferences.pageMargins!!.toFloat())
        } else {
            editor.remove(KEY_MARGINS)
        }

        editor.apply()
    }

    private fun applyPreferences() {
        navigator?.submitPreferences(currentPreferences)
    }

    private fun showFontSettings() {
        val nav = navigator ?: return

        val dialogView = layoutInflater.inflate(R.layout.dialog_display_settings, null)

        // Tab switching
        val tabLayout = dialogView.findViewById<com.google.android.material.tabs.TabLayout>(R.id.tab_layout)
        val textContent = dialogView.findViewById<View>(R.id.tab_text_content)
        val displayContent = dialogView.findViewById<View>(R.id.tab_display_content)

        tabLayout.addOnTabSelectedListener(object : com.google.android.material.tabs.TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: com.google.android.material.tabs.TabLayout.Tab?) {
                when (tab?.position) {
                    0 -> {
                        textContent.visibility = View.VISIBLE
                        displayContent.visibility = View.GONE
                    }
                    1 -> {
                        textContent.visibility = View.GONE
                        displayContent.visibility = View.VISIBLE
                    }
                }
            }
            override fun onTabUnselected(tab: com.google.android.material.tabs.TabLayout.Tab?) {}
            override fun onTabReselected(tab: com.google.android.material.tabs.TabLayout.Tab?) {}
        })

        // ======== TEXT TAB ========

        // Font Family toggle group
        val fontGroup = dialogView.findViewById<com.google.android.material.button.MaterialButtonToggleGroup>(R.id.font_family_group)
        val btnFontSystem = dialogView.findViewById<com.google.android.material.button.MaterialButton>(R.id.btn_font_system)
        val btnFontSerif = dialogView.findViewById<com.google.android.material.button.MaterialButton>(R.id.btn_font_serif)
        val btnFontSans = dialogView.findViewById<com.google.android.material.button.MaterialButton>(R.id.btn_font_sans)

        // Pre-select current font
        when (currentPreferences.fontFamily) {
            FontFamily.SERIF -> fontGroup.check(R.id.btn_font_serif)
            FontFamily.SANS_SERIF -> fontGroup.check(R.id.btn_font_sans)
            else -> fontGroup.check(R.id.btn_font_system)
        }

        fontGroup.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked) {
                val fontFamily = when (checkedId) {
                    R.id.btn_font_serif -> FontFamily.SERIF
                    R.id.btn_font_sans -> FontFamily.SANS_SERIF
                    else -> null
                }
                currentPreferences = currentPreferences.copy(fontFamily = fontFamily)
                nav.submitPreferences(currentPreferences)
                savePreferences()
            }
        }

        // Font Size
        val btnFontDecrease = dialogView.findViewById<View>(R.id.btn_font_decrease)
        val btnFontIncrease = dialogView.findViewById<View>(R.id.btn_font_increase)
        val btnFontReset = dialogView.findViewById<View>(R.id.btn_font_reset)
        val textFontSize = dialogView.findViewById<TextView>(R.id.text_font_size)

        fun updateFontSize(newSize: Double?) {
            currentPreferences = currentPreferences.copy(fontSize = newSize)
            textFontSize.text = if (newSize != null) "${(newSize * 100).toInt()}%" else "100%"
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        textFontSize.text = "${((currentPreferences.fontSize ?: 1.0) * 100).toInt()}%"

        btnFontDecrease.setOnClickListener {
            val current = currentPreferences.fontSize ?: 1.0
            updateFontSize((current - 0.1).coerceAtLeast(0.5))
        }
        btnFontIncrease.setOnClickListener {
            val current = currentPreferences.fontSize ?: 1.0
            updateFontSize((current + 0.1).coerceAtMost(3.0))
        }
        btnFontReset.setOnClickListener { updateFontSize(null) }

        // Line Spacing
        val btnSpacingDecrease = dialogView.findViewById<View>(R.id.btn_spacing_decrease)
        val btnSpacingIncrease = dialogView.findViewById<View>(R.id.btn_spacing_increase)
        val btnSpacingReset = dialogView.findViewById<View>(R.id.btn_spacing_reset)
        val textSpacing = dialogView.findViewById<TextView>(R.id.text_spacing)

        fun updateSpacing(newSpacing: Double?) {
            currentPreferences = currentPreferences.copy(lineHeight = newSpacing)
            textSpacing.text = if (newSpacing != null) "%.1fx".format(newSpacing) else "1.2x"
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        textSpacing.text = "%.1fx".format(currentPreferences.lineHeight ?: 1.2)

        btnSpacingDecrease.setOnClickListener {
            val current = currentPreferences.lineHeight ?: 1.2
            updateSpacing((current - 0.1).coerceAtLeast(1.0))
        }
        btnSpacingIncrease.setOnClickListener {
            val current = currentPreferences.lineHeight ?: 1.2
            updateSpacing((current + 0.1).coerceAtMost(2.5))
        }
        btnSpacingReset.setOnClickListener { updateSpacing(null) }

        // Margins
        val btnMarginDecrease = dialogView.findViewById<View>(R.id.btn_margin_decrease)
        val btnMarginIncrease = dialogView.findViewById<View>(R.id.btn_margin_increase)
        val btnMarginReset = dialogView.findViewById<View>(R.id.btn_margin_reset)
        val textMargins = dialogView.findViewById<TextView>(R.id.text_margins)

        fun updateMargins(newMargins: Double?) {
            currentPreferences = currentPreferences.copy(pageMargins = newMargins)
            textMargins.text = if (newMargins != null) "%.2fx".format(newMargins) else "1.00x"
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        textMargins.text = "%.2fx".format(currentPreferences.pageMargins ?: 1.0)

        btnMarginDecrease.setOnClickListener {
            val current = currentPreferences.pageMargins ?: 1.0
            updateMargins((current - 0.25).coerceAtLeast(0.5))
        }
        btnMarginIncrease.setOnClickListener {
            val current = currentPreferences.pageMargins ?: 1.0
            updateMargins((current + 0.25).coerceAtMost(3.0))
        }
        btnMarginReset.setOnClickListener { updateMargins(null) }

        // ======== DISPLAY TAB ========

        val btnThemeLight = dialogView.findViewById<View>(R.id.btn_theme_light)
        val btnThemeSepia = dialogView.findViewById<View>(R.id.btn_theme_sepia)
        val btnThemeDark = dialogView.findViewById<View>(R.id.btn_theme_dark)
        val checkLight = dialogView.findViewById<View>(R.id.check_theme_light)
        val checkSepia = dialogView.findViewById<View>(R.id.check_theme_sepia)
        val checkDark = dialogView.findViewById<View>(R.id.check_theme_dark)

        fun updateThemeChecks(theme: Theme?) {
            checkLight.visibility = if (theme == Theme.LIGHT) View.VISIBLE else View.GONE
            checkSepia.visibility = if (theme == Theme.SEPIA) View.VISIBLE else View.GONE
            checkDark.visibility = if (theme == Theme.DARK || theme == null) View.VISIBLE else View.GONE
        }

        // Show current checkmark
        updateThemeChecks(currentPreferences.theme)

        fun applyTheme(theme: Theme?) {
            currentPreferences = currentPreferences.copy(theme = theme)
            nav.submitPreferences(currentPreferences)
            savePreferences()
            updateThemeChecks(theme)
        }

        btnThemeLight.setOnClickListener { applyTheme(Theme.LIGHT) }
        btnThemeSepia.setOnClickListener { applyTheme(Theme.SEPIA) }
        btnThemeDark.setOnClickListener { applyTheme(Theme.DARK) }

        val dialog = MaterialAlertDialogBuilder(this)
            .setView(dialogView)
            .create()

        dialog.show()
    }

    // ============ Text Selection Sync ============
    //
    // The floating selection toolbar (Copy / Share / Select all / etc.) is
    // driven by an `ActionMode.Callback` that the WebView starts when the user
    // long-presses text. To strip noise items and inject our own BEFORE the
    // toolbar takes its menu snapshot, we have to intercept the *creation* of
    // the ActionMode — not mutate the menu after.
    //
    // System Chrome WebView does NOT route this through
    // `Window.Callback.onWindowStartingActionMode`; tested empirically and
    // also documented behavior. Instead, the WebView calls
    // `View.startActionMode(callback, TYPE_FLOATING)`, which walks up via
    // `ViewParent.startActionModeForChild(...)`. Each ancestor ViewGroup gets
    // a chance to intercept. So we install a custom intercepting FrameLayout
    // between the WebView and its current parent at runtime — see
    // `installSelectionInterceptor()`.
    //
    // `onActionModeStarted` is also kept as a defensive fallback: it adds
    // Define / Sync-to-Audio post-hoc so they're at least available even if
    // the interceptor wasn't installed (e.g. WebView was recreated, etc.).
    // Mutate-after-snapshot can't refresh the rendered toolbar, so the noise
    // strip path lives only inside the wrapper. See plan in
    // `.claude/plans/playful-painting-salamander.md`.

    /**
     * Install ONE [SelectionInterceptingFrameLayout] around the activity's
     * content root, so we intercept TYPE_FLOATING ActionMode creation via
     * `startActionModeForChild`.
     *
     * Key fact: `startActionModeForChild` PROPAGATES UP the whole view
     * hierarchy (each ViewGroup delegates to its parent until the DecorView
     * creates the FloatingActionMode). So we don't need to wrap each of
     * Readium's per-page WebViews — a single wrapper around the activity
     * content root sees every selection from every WebView, including pages
     * created later by the pager. The content root exists from setContentView
     * and is never recreated. Idempotent; onResume() re-calls as a no-op.
     */
    private var hasInstalledSelectionInterceptor: Boolean = false

    private fun installSelectionInterceptor() {
        if (hasInstalledSelectionInterceptor) return
        val content = findViewById<ViewGroup>(android.R.id.content) ?: return
        val root = content.getChildAt(0) ?: return
        if (root is SelectionInterceptingFrameLayout) {
            hasInstalledSelectionInterceptor = true
            return
        }
        val params = root.layoutParams
        content.removeView(root)
        val interceptor = SelectionInterceptingFrameLayout(this).apply {
            // Don't consume touches ourselves.
            isClickable = false
            isFocusable = false
            addView(
                root,
                android.widget.FrameLayout.LayoutParams(
                    android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                    android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                ),
            )
        }
        content.addView(interceptor, params)
        hasInstalledSelectionInterceptor = true
        Log.d(TAG, "Selection interceptor installed at activity content root")
    }

    /**
     * Custom ViewGroup ancestor of the WebView. Chrome WebView calls
     * `parent.startActionModeForChild(view, callback, TYPE_FLOATING)` when a
     * text selection happens. By overriding here, we get to wrap the callback
     * BEFORE the ActionMode is created and BEFORE the FloatingToolbar takes
     * its menu snapshot — which is the only point at which menu mutations
     * actually affect the rendered toolbar.
     */
    private inner class SelectionInterceptingFrameLayout(
        context: android.content.Context,
    ) : android.widget.FrameLayout(context) {

        override fun startActionModeForChild(
            originalView: android.view.View,
            callback: android.view.ActionMode.Callback,
            type: Int,
        ): android.view.ActionMode? {
            if (type == android.view.ActionMode.TYPE_FLOATING) {
                Log.d(TAG, "Intercepting startActionModeForChild type=$type (selection toolbar)")
                return super.startActionModeForChild(
                    originalView,
                    SelectionCallbackWrapper(callback),
                    type,
                )
            }
            return super.startActionModeForChild(originalView, callback, type)
        }

        // Older overload — WebView always passes a type on API 23+, so this
        // typically isn't hit, but override defensively.
        override fun startActionModeForChild(
            originalView: android.view.View,
            callback: android.view.ActionMode.Callback,
        ): android.view.ActionMode? {
            return super.startActionModeForChild(originalView, callback)
        }
    }

    /**
     * Defensive fallback: if the interceptor isn't installed (e.g. WebView
     * lifecycle edge case, or device WebView routes through a different code
     * path), this still injects our custom items so they're at least
     * reachable. We don't bother trimming here because mutate-after-snapshot
     * doesn't refresh the rendered toolbar.
     */
    override fun onActionModeStarted(mode: android.view.ActionMode?) {
        super.onActionModeStarted(mode)
        if (mode == null) return
        captureSelection()
        val menu = mode.menu ?: return
        trimSelectionMenu(menu)
        injectCustomItems(mode, menu)
        // Re-render the floating toolbar so our injected items are visible.
        // Without this, items added after the initial onCreateActionMode snapshot
        // are silently ignored by the FloatingToolbar.
        mode.invalidate()
    }

    /**
     * Wraps the WebView's selection ActionMode callback so we can:
     *   - Strip Share / Select All / Translate / Web Search from the Menu
     *     before the floating toolbar ever snapshots it.
     *   - Re-strip on `onPrepareActionMode` for Android 14+ async text-classifier
     *     items (those arrive later via a second prepare pass).
     *   - Inject our own "Define" + "Sync to Audio" items.
     *   - Delegate Copy / positioning / destroy to the original callback.
     *
     * Must extend `Callback2`, not the plain `Callback` interface, so
     * `onGetContentRect` is forwarded — otherwise the toolbar mis-positions
     * away from the selection.
     */
    private inner class SelectionCallbackWrapper(
        private val delegate: android.view.ActionMode.Callback,
    ) : android.view.ActionMode.Callback2() {

        override fun onCreateActionMode(
            mode: android.view.ActionMode,
            menu: android.view.Menu,
        ): Boolean {
            // Let the WebView populate first so we can edit the result.
            val keep = delegate.onCreateActionMode(mode, menu)
            captureSelection()
            trimSelectionMenu(menu)
            injectCustomItems(mode, menu)
            return keep || true
        }

        override fun onPrepareActionMode(
            mode: android.view.ActionMode,
            menu: android.view.Menu,
        ): Boolean {
            // Async TextClassifier items (API 29+) come in via a follow-up prepare
            // cycle. Re-strip + re-inject so the rendered toolbar stays clean.
            delegate.onPrepareActionMode(mode, menu)
            trimSelectionMenu(menu)
            injectCustomItems(mode, menu)
            return true
        }

        override fun onActionItemClicked(
            mode: android.view.ActionMode,
            item: android.view.MenuItem,
        ): Boolean {
            // Our injected items consume their clicks via setOnMenuItemClickListener,
            // so this only fires for Copy / Read Aloud / etc. — delegate as-is.
            return delegate.onActionItemClicked(mode, item)
        }

        override fun onDestroyActionMode(mode: android.view.ActionMode) {
            delegate.onDestroyActionMode(mode)
        }

        override fun onGetContentRect(
            mode: android.view.ActionMode,
            view: android.view.View?,
            outRect: android.graphics.Rect,
        ) {
            // Forward when possible so the toolbar anchors to the selection.
            // Falling back to super positions the toolbar at the view origin,
            // which looks broken — but better than crashing.
            if (delegate is android.view.ActionMode.Callback2) {
                delegate.onGetContentRect(mode, view, outRect)
            } else {
                super.onGetContentRect(mode, view, outRect)
            }
        }
    }

    /**
     * Read the selection stored by the selectionchange tracker injected in
     * savePosition. `window.getSelection()` here is unreliable (ActionMode may
     * have cleared the DOM selection by the time JS evaluates), so we prefer
     * `window._bookSyncSelection` which the tracker captures the moment the
     * user makes a selection.
     */
    private fun captureSelection() {
        val webView = navigator?.view?.let { findWebView(it) } ?: return
        webView.evaluateJavascript("""
            (function() {
                var stored = window._bookSyncSelection || '';
                if (stored.trim().length > 3) return stored;
                var frames = document.querySelectorAll('iframe');
                for (var i = 0; i < frames.length; i++) {
                    try {
                        var sel = frames[i].contentWindow.getSelection().toString().trim();
                        if (sel.length > 3) return sel;
                    } catch(e) {}
                }
                return window.getSelection().toString();
            })()
        """.trimIndent()) { result ->
            val captured = result?.trim('"')?.replace("\\n", " ")?.trim() ?: ""
            if (captured.isNotEmpty()) {
                lastSelectedText = captured
                Log.d(TAG, "Captured selection: '${captured.take(60)}'")
            }
        }
    }

    /**
     * Insert the reader's two custom selection actions: Define (order 0,
     * leftmost) and Sync to Audio (order 1). Idempotent via `findItem` —
     * safe to call from both `onCreateActionMode` and `onPrepareActionMode`,
     * and from the `onActionModeStarted` fallback.
     *
     * Sync to Audio is only injected when there is a downloaded paired
     * audiobook, since it has nothing to scrub to otherwise.
     */
    private fun injectCustomItems(mode: android.view.ActionMode, menu: android.view.Menu) {
        if (menu.findItem(R.id.action_define) == null) {
            menu.add(0, R.id.action_define, 0, "Define").setOnMenuItemClickListener {
                defineSelectedWord()
                mode.finish()
                true
            }
            Log.d(TAG, "Added 'Define' to ActionMode menu")
        }
        if (pair?.audiobookDownloaded == true &&
            menu.findItem(R.id.action_sync_selection) == null
        ) {
            menu.add(0, R.id.action_sync_selection, 1, "Sync to Audio").setOnMenuItemClickListener {
                syncSelectedTextToAudio()
                mode.finish()
                true
            }
            Log.d(TAG, "Added 'Sync to Audio' to ActionMode menu")
        }
    }

    /**
     * Remove Web Search / Select All / Share / Translate / Assist from the
     * floating selection toolbar. We keep Copy (android.R.id.copy) so users
     * can still quote a passage, and we leave anything we don't recognize
     * alone so accessibility items like "Read Aloud" stay available.
     *
     * The system populates these items dynamically (some on Android 14+ from
     * text classification), so we match by id AND by a loose title contains
     * check to catch variants like "Share…", "Search web", or locale strings.
     */
    private fun trimSelectionMenu(menu: android.view.Menu?) {
        menu ?: return
        val knownNoiseIds = setOf(
            android.R.id.shareText,
            android.R.id.selectAll,
            // android.R.id.textAssist (= 0x1020041) is the slot the system
            // TextClassifier uses to inject "smart" suggestions like a
            // Google-branded "Define" or "Translate" chip. We have our own
            // Define / Sync to Audio actions, so strip whatever the
            // classifier picks here unconditionally. Without this strip a
            // "G Define" appears next to ours on the second-or-later
            // selection (after the async classifier pass finishes).
            android.R.id.textAssist,
            // Some OEMs use non-android-framework ids for these text-classifier
            // items; match by title below catches them.
        )
        // Substrings (case-insensitive) to match against the item title.
        // Use contains rather than exact match so we catch "Share…",
        // "Select all", "Search web", OEM-specific labels, etc.
        val noiseTitleSubstrings = listOf(
            "share", "select all", "translate",
            "web search", "search web", "assist",
        )
        val itemsToRemove = mutableListOf<Int>()
        for (i in 0 until menu.size()) {
            val item = menu.getItem(i) ?: continue
            val itemId = item.itemId
            val title = item.title?.toString().orEmpty()
            val titleLower = title.lowercase().trim().trimEnd('\u2026', '.', ' ')
            Log.v(TAG, "Selection menu item: id=0x${itemId.toString(16)} title='$title'")
            // Don't touch our own custom items
            if (itemId == R.id.action_define || itemId == R.id.action_sync_selection) continue
            // Don't touch Copy — users still need it
            if (itemId == android.R.id.copy) continue
            // Leave Read Aloud / accessibility items alone
            if ("read aloud" in titleLower || "speak" in titleLower) continue
            val matchesId = itemId in knownNoiseIds
            val matchesTitle = noiseTitleSubstrings.any { it in titleLower }
            if (matchesId || matchesTitle) itemsToRemove += itemId
        }
        itemsToRemove.forEach { menu.removeItem(it) }
        if (itemsToRemove.isNotEmpty()) {
            Log.d(TAG, "Stripped ${itemsToRemove.size} noise item(s) from selection toolbar")
        }
    }

    /**
     * Look up the first word of the user's selection on dictionaryapi.dev
     * and present the result in a dialog. Silent/Toast on offline or 404.
     */
    private fun defineSelectedWord() {
        val raw = lastSelectedText.trim()
        // Take the first "wordy" token — strip trailing/leading punctuation, pick first whitespace-separated chunk.
        val firstToken = raw.split(Regex("\\s+"))
            .firstOrNull()
            ?.trim { !it.isLetter() && it != '\'' && it != '-' }
            .orEmpty()

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

    private fun syncSelectedTextToAudio() {
        // Use text captured in onActionModeStarted — by the time this click fires,
        // the ActionMode interaction has already cleared window.getSelection().
        val selectedText = lastSelectedText.trim()
        Log.d(TAG, "Selected text: '${selectedText.take(100)}'")

        if (selectedText.length < 5) {
            android.widget.Toast.makeText(this, "Select more text to sync", android.widget.Toast.LENGTH_SHORT).show()
            return
        }

        val locator = navigator?.currentLocator?.value ?: return
        val pub = publication ?: return
        val chapterIndex = pub.spineIndexOf(locator).coerceAtLeast(0)
        Log.d(TAG, "syncSelectedText: chapterIndex=$chapterIndex, text='${selectedText.take(60)}'")

        lifecycleScope.launch {
            val audioMs = repository.epubToAudioText(pairId, chapterIndex, selectedText, rewindMs = 2000)
            if (audioMs > 0) {
                Log.d(TAG, "syncSelectedText: matched audioMs=$audioMs (${formatAudioTime(audioMs.toLong())})")
                sentenceSyncPending = true
                SyncState.pendingAudioSeekMs = audioMs.toLong()
                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = chapterIndex,
                    audioPositionMs = audioMs,
                    epubLocator = locator.toJSON().toString(),
                    // Pair the page the user is on with the synced audio position so
                    // returning from the player within ~30s lands on this exact page.
                    locatorAudioMs = audioMs,
                )
                val timeStr = formatAudioTime(audioMs.toLong())
                android.widget.Toast.makeText(this@ReaderActivity, "Audio synced to $timeStr", android.widget.Toast.LENGTH_SHORT).show()
                // After successful sync, jump straight to the player so the user can continue listening.
                switchToAudio()
            } else {
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
    private fun injectSelectionTracker() {
        val webView = navigator?.view?.let { findWebView(it) } ?: return
        webView.evaluateJavascript(SELECTION_TRACKER_JS) {}
    }

    private fun findWebView(view: View): android.webkit.WebView? {
        if (view is android.webkit.WebView) return view
        if (view is ViewGroup) {
            for (i in 0 until view.childCount) {
                val result = findWebView(view.getChildAt(i))
                if (result != null) return result
            }
        }
        return null
    }

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
