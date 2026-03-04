package com.booksync.ui.reader

import android.content.Context
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
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
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
        private const val PREFS_NAME = "reader_display"
        private const val SYNC_PREFS_NAME = "sync_calibration"
        private const val KEY_FONT_SIZE = "font_size"
        private const val KEY_THEME = "theme"
        private const val KEY_FONT_FAMILY = "font_family"
        private const val KEY_LINE_SPACING = "line_spacing"
        private const val KEY_MARGINS = "margins"
    }

    @Inject lateinit var repository: BookSyncRepository

    private var publication: Publication? = null
    private var navigator: EpubNavigatorFragment? = null
    private var pair: BookPairEntity? = null
    private var pairId: Int = 0
    private var positionSaveJob: Job? = null
    private var isBarVisible = false
    private var isSeeking = false
    private var syncChapterOffset = 0 // Offset between SyncMap epub_chapter and Readium spine index
    private val chapterTextCache = mutableMapOf<Int, String?>() // spine index → plain text cache
    /** When true, savePosition skips overwriting the audio bookmark (preserves sentence sync). */
    private var sentenceSyncPending = false

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
            super.onCreate(savedInstanceState)
            Log.w(TAG, "Process death detected, finishing")
            finish()
            return
        }

        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_reader)

        loadSavedPreferences()
        initViews()
        applyWindowInsets()
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
                R.id.action_sync -> { syncAudioToPage(); true }
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
                    ?: run { Log.e(TAG, "Failed to retrieve asset"); finish(); return@launch }

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

                calibrateSyncOffset()

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



            } catch (e: Exception) {
                Log.e(TAG, "Error loading publication", e)
                finish()
            }
        }
    }

    private suspend fun calibrateSyncOffset() {
        if (pair?.audiobookDownloaded != true) return
        
        // Check for cached calibration first (avoids expensive startup scan)
        val syncPrefs = getSharedPreferences(SYNC_PREFS_NAME, Context.MODE_PRIVATE)
        val cachedOffset = syncPrefs.getInt("sync_offset_$pairId", Int.MIN_VALUE)
        if (cachedOffset != Int.MIN_VALUE) {
            syncChapterOffset = cachedOffset
            Log.d(TAG, "Using cached sync offset: $syncChapterOffset for pairId=$pairId")
            return
        }
        
        val points = repository.getSyncPoints(pairId)
        
        // Only use points whose preview clearly starts with a chapter heading
        // This reliably excludes front matter (praise, copyright, dedication, etc.)
        val chapterPattern = Regex("^(CHAPTER\\s+\\d+|PROLOGUE)", RegexOption.IGNORE_CASE)
        val validPoints = points.filter { 
            val preview = it.epubTextPreview ?: ""
            preview.length > 60 && chapterPattern.containsMatchIn(preview.trim())
        }
        Log.d(TAG, "calibrateSyncOffset: ${validPoints.size} valid content chapter points out of ${points.size} total")
        if (validPoints.isEmpty()) return
        
        val pub = publication ?: return
        
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            val offsets = mutableListOf<Int>()
            val sampledPoints = validPoints.groupBy { it.epubChapter }
                .values.map { it.first() }.take(10)
            
            for (targetPoint in sampledPoints) {
                val rawPreview = targetPoint.epubTextPreview!!.trim()
                val cleanPreview = rawPreview.replace(Regex("^(CHAPTER\\s+\\d+|PROLOGUE)[\\s\\n,.]*", RegexOption.IGNORE_CASE), "").take(60).trim()
                if (cleanPreview.length < 20) continue
                val searchText = cleanPreview.replace("\n", " ").replace(Regex("\\s+"), " ")
                
                for (i in pub.readingOrder.indices) {
                    val plainText = getChapterPlainText(i) ?: continue
                    if (plainText.contains(searchText, ignoreCase = true)) {
                        val offset = i - targetPoint.epubChapter
                        offsets.add(offset)
                        Log.d(TAG, "calibrateSyncOffset sample: syncCh=${targetPoint.epubChapter} → spine=$i, offset=$offset, text='${searchText.take(40)}'")
                        break
                    }
                }
            }
            
            if (offsets.isNotEmpty()) {
                syncChapterOffset = offsets.groupingBy { it }.eachCount().maxByOrNull { it.value }!!.key
                Log.d(TAG, "Calibrated sync offset: $syncChapterOffset (from ${offsets.size} samples: $offsets)")
                // Persist for future launches
                syncPrefs.edit().putInt("sync_offset_$pairId", syncChapterOffset).apply()
            }
        }
    }

    /** Find which spine index contains the given text preview.
     *  Searches outward from hintIdx (syncChapter + offset) to prefer nearby matches. */
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

        positionSaveJob?.cancel()
        positionSaveJob = lifecycleScope.launch {
            var lastSaveTime = 0L
            nav.currentLocator.collect { locator ->
                // Update progress UI
                updateProgressUI(locator)


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

        // Map overall progress to a chapter in the reading order
        val readingOrder = pub.readingOrder
        if (readingOrder.isEmpty()) return

        val targetIndex = (progress * readingOrder.size).toInt().coerceIn(0, readingOrder.size - 1)
        val link = readingOrder[targetIndex]
        val locator = pub.locatorFromLink(link) ?: return
        nav.go(locator, animated = false)
    }

    // ============ Bookmark save/restore ============

    private suspend fun getInitialLocator(pub: Publication): Locator? {
        return try {
            val bookmark = repository.getBookmarkFlow(pairId).firstOrNull()
            Log.d(TAG, "Bookmark loaded: source=${bookmark?.source} epubLocator=${bookmark?.epubLocator?.take(80)}")
            
            if (bookmark?.source == "audiobook" && bookmark.audioPositionMs != null) {
                val (syncChapter, previewText) = repository.audioToEpubText(pairId, bookmark.audioPositionMs)
                Log.d(TAG, "getInitialLocator: audioPos=${bookmark.audioPositionMs}ms => syncChapter=$syncChapter, preview='${previewText.take(60)}'")
                // Find correct spine index via text search, searching near expected chapter
                val hintIdx = syncChapter + syncChapterOffset
                val chapterIdx = findSpineIndexForText(previewText, hintIdx)
                    ?: hintIdx.takeIf { it in pub.readingOrder.indices }
                Log.d(TAG, "getInitialLocator: hintIdx=$hintIdx, resolved chapterIdx=$chapterIdx")
                if (chapterIdx != null && chapterIdx in pub.readingOrder.indices) {
                    val link = pub.readingOrder[chapterIdx]
                    val baseLocator = pub.locatorFromLink(link)
                    if (baseLocator != null && previewText.isNotEmpty()) {
                        val progressionVal = findTextProgressionInChapter(chapterIdx, previewText) ?: 0.0
                        return baseLocator.copy(locations = Locator.Locations(progression = progressionVal))
                    }
                }
            }

            val locatorJson = bookmark?.epubLocator
            if (locatorJson != null) {
                Locator.fromJSON(org.json.JSONObject(locatorJson))
            } else null
        } catch (e: Exception) {
            Log.w(TAG, "Error getting initial locator", e)
            null
        }
    }

    private fun savePosition(locator: Locator) {
        val bookPair = pair ?: return
        lifecycleScope.launch {
            try {
                val pub = publication ?: return@launch
                val rawChapterIndex = pub.readingOrder.indexOfFirst { 
                    val pubHref = it.href.toString()
                    val locHref = locator.href.toString()
                    pubHref == locHref || pubHref.endsWith(locHref.substringAfterLast("/")) || locHref.endsWith(pubHref.substringAfterLast("/"))
                }
                val chapterIndex = rawChapterIndex.coerceAtLeast(0)
                val syncChapter = (chapterIndex - syncChapterOffset).coerceAtLeast(0)
                Log.d(TAG, "savePosition: locator.href='${locator.href}', rawIndex=$rawChapterIndex, chapterIndex=$chapterIndex, syncChapter=$syncChapter, progression=${locator.locations.progression}")
            
                val progression = locator.locations.progression ?: 0.0
                val plainText = getChapterPlainText(chapterIndex)
                val textPreview = if (plainText != null) {
                    val charIndex = (plainText.length * progression).toInt()
                    val startIndex = maxOf(0, charIndex - 20)
                    val endIndex = minOf(charIndex + 200, plainText.length)
                    // Extract a focused window, strip chapter headings
                    // Handle "CHAPTER N, Title CHAPTER N" pattern (Jsoup has no newlines)
                    plainText.substring(startIndex, endIndex)
                        .replace(Regex("(?i)^chapter\\s+\\d+.{0,120}?chapter\\s+\\d+\\s*"), "")
                        .replace(Regex("(?i)^chapter\\s+\\d+[,.]?\\s*"), "")
                        .replace(Regex("(?i)^prologue[,.]?\\s*"), "")
                        .trim()
                } else ""
                
                val locatorJson = locator.toJSON().toString()

            if (sentenceSyncPending) {
                // Don't overwrite the sentence-level audio bookmark;
                // just save the epub locator so we can restore the page position.
                // Flag stays true for the rest of the activity lifecycle.
                Log.d(TAG, "savePosition: sentenceSyncPending=true, skipping audio sync")
                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = syncChapter,
                    epubLocator = locatorJson,
                )
            } else {
                val syncPoint = repository.getSyncPointForEpubText(pairId, syncChapter, textPreview)
                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = syncChapter,
                    epubSentenceIndex = syncPoint?.epubSentenceIndex ?: 0,
                    audioPositionMs = syncPoint?.audioStartMs,
                    epubLocator = locatorJson,
                )
            }
            } catch (e: Exception) {
                Log.w(TAG, "Error saving position", e)
            }
        }
    }

    // ============ Manual Sync ============

    private fun syncAudioToPage() {
        if (pair?.audiobookDownloaded != true) {
            android.widget.Toast.makeText(this, "Audiobook not downloaded", android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        val locator = navigator?.currentLocator?.value ?: return
        val pub = publication ?: return
        
        // Find chapter using robust matching
        val rawChapterIndex = pub.readingOrder.indexOfFirst { 
            val pubHref = it.href.toString()
            val locHref = locator.href.toString()
            pubHref == locHref || pubHref.endsWith(locHref.substringAfterLast("/")) || locHref.endsWith(pubHref.substringAfterLast("/"))
        }
        val chapterIndex = rawChapterIndex.coerceAtLeast(0)
        val syncChapter = (chapterIndex - syncChapterOffset).coerceAtLeast(0)
        
        val progression = locator.locations.progression ?: 0.0
        Log.d(TAG, "syncAudioToPage called! locator.href='${locator.href}', progression=$progression")
        Log.d(TAG, "syncAudioToPage: rawChapterIndex=$rawChapterIndex => chapterIndex=$chapterIndex, syncChapterOffset=$syncChapterOffset => syncChapter=$syncChapter")
    
        lifecycleScope.launch {
            val plainText = getChapterPlainText(chapterIndex)
            val textPreview = if (plainText != null) {
                val charIndex = (plainText.length * progression).toInt()
                val startIndex = maxOf(0, charIndex - 20)
                val endIndex = minOf(charIndex + 200, plainText.length)
                // Extract a focused window, strip chapter headings
                // Handle "CHAPTER N, Title CHAPTER N" pattern (Jsoup has no newlines)
                plainText.substring(startIndex, endIndex)
                    .replace(Regex("(?i)^chapter\\s+\\d+.{0,120}?chapter\\s+\\d+\\s*"), "")
                    .replace(Regex("(?i)^chapter\\s+\\d+[,.]?\\s*"), "")
                    .replace(Regex("(?i)^prologue[,.]?\\s*"), "")
                    .trim()
            } else ""

            val audioMs = repository.epubToAudioText(pairId, syncChapter, textPreview, rewindMs = 2000)
            if (audioMs > 0) {
                // Save audio position as bookmark so the player picks it up
                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = syncChapter,
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
        val fontSizeText = dialogView.findViewById<TextView>(R.id.text_font_size)
        val btnDecrease = dialogView.findViewById<View>(R.id.btn_font_decrease)
        val btnIncrease = dialogView.findViewById<View>(R.id.btn_font_increase)
        
        val btnThemeLight = dialogView.findViewById<View>(R.id.btn_theme_light)
        val btnThemeSepia = dialogView.findViewById<View>(R.id.btn_theme_sepia)
        val btnThemeDark = dialogView.findViewById<View>(R.id.btn_theme_dark)

        val btnFontSystem = dialogView.findViewById<View>(R.id.btn_font_system)
        val btnFontSerif = dialogView.findViewById<View>(R.id.btn_font_serif)
        val btnFontSans = dialogView.findViewById<View>(R.id.btn_font_sans)

        val btnSpacing10 = dialogView.findViewById<View>(R.id.btn_spacing_10)
        val btnSpacing15 = dialogView.findViewById<View>(R.id.btn_spacing_15)
        val btnSpacing20 = dialogView.findViewById<View>(R.id.btn_spacing_20)

        val btnMarginNarrow = dialogView.findViewById<View>(R.id.btn_margin_narrow)
        val btnMarginNormal = dialogView.findViewById<View>(R.id.btn_margin_normal)
        val btnMarginWide = dialogView.findViewById<View>(R.id.btn_margin_wide)

        // Show current size
        val currentSize = currentPreferences.fontSize ?: 1.0
        fontSizeText.text = "${(currentSize * 100).toInt()}%"

        val dialog = MaterialAlertDialogBuilder(this)
            .setView(dialogView)
            .create()

        btnDecrease.setOnClickListener {
            val newSize = ((currentPreferences.fontSize ?: 1.0) - 0.1).coerceAtLeast(0.5)
            currentPreferences = currentPreferences.copy(fontSize = newSize)
            fontSizeText.text = "${(newSize * 100).toInt()}%"
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }

        btnIncrease.setOnClickListener {
            val newSize = ((currentPreferences.fontSize ?: 1.0) + 0.1).coerceAtMost(3.0)
            currentPreferences = currentPreferences.copy(fontSize = newSize)
            fontSizeText.text = "${(newSize * 100).toInt()}%"
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }

        // Theme Setters
        fun updateTheme(theme: Theme?) {
            currentPreferences = currentPreferences.copy(theme = theme)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        btnThemeLight.setOnClickListener { updateTheme(Theme.LIGHT) }
        btnThemeSepia.setOnClickListener { updateTheme(Theme.SEPIA) }
        btnThemeDark.setOnClickListener { updateTheme(Theme.DARK) }

        // Font Family Setters
        fun updateFontFamily(fontFamily: FontFamily?) {
            currentPreferences = currentPreferences.copy(fontFamily = fontFamily)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        btnFontSystem.setOnClickListener { updateFontFamily(null) }
        btnFontSerif.setOnClickListener { updateFontFamily(FontFamily.SERIF) }
        btnFontSans.setOnClickListener { updateFontFamily(FontFamily.SANS_SERIF) }

        // Line Spacing Setters
        fun updateLineSpacing(spacing: Double?) {
            currentPreferences = currentPreferences.copy(lineHeight = spacing)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        btnSpacing10.setOnClickListener { updateLineSpacing(1.0) }
        btnSpacing15.setOnClickListener { updateLineSpacing(1.5) }
        btnSpacing20.setOnClickListener { updateLineSpacing(2.0) }

        // Page Margins Setters
        fun updateMargins(margins: Double?) {
            currentPreferences = currentPreferences.copy(pageMargins = margins)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        btnMarginNarrow.setOnClickListener { updateMargins(0.5) }
        btnMarginNormal.setOnClickListener { updateMargins(1.0) }
        btnMarginWide.setOnClickListener { updateMargins(2.0) }

        dialog.show()
    }

    // ============ Text Selection Sync ============

    override fun onActionModeStarted(mode: android.view.ActionMode?) {
        super.onActionModeStarted(mode)
        if (mode == null || pair?.audiobookDownloaded != true) return

        // Add our custom item and force the floating toolbar to refresh
        if (mode.menu?.findItem(R.id.action_sync_selection) == null) {
            val item = mode.menu?.add(0, R.id.action_sync_selection, 0, "Sync to Audio")
            item?.setOnMenuItemClickListener {
                syncSelectedTextToAudio()
                mode.finish()
                true
            }
            Log.d(TAG, "Added 'Sync to Audio' to ActionMode menu")
        }
        // Force floating toolbar to rebuild with the new item
        mode.invalidate()
    }

    private fun syncSelectedTextToAudio() {
        // Get selected text from the WebView inside the navigator fragment
        val webView = navigator?.view?.let { findWebView(it) }
        if (webView == null) {
            Log.w(TAG, "Could not find WebView for text selection")
            return
        }

        webView.evaluateJavascript("window.getSelection().toString()") { result ->
            // Result comes back as a JSON string with quotes
            val selectedText = result?.trim('"')?.replace("\\n", " ")?.trim() ?: ""
            Log.d(TAG, "Selected text: '${selectedText.take(100)}'")

            if (selectedText.length < 5) {
                android.widget.Toast.makeText(this, "Select more text to sync", android.widget.Toast.LENGTH_SHORT).show()
                return@evaluateJavascript
            }

            // Find current chapter for hint
            val locator = navigator?.currentLocator?.value ?: return@evaluateJavascript
            val pub = publication ?: return@evaluateJavascript
            val rawChapterIndex = pub.readingOrder.indexOfFirst {
                val pubHref = it.href.toString()
                val locHref = locator.href.toString()
                pubHref == locHref || pubHref.endsWith(locHref.substringAfterLast("/")) || locHref.endsWith(pubHref.substringAfterLast("/"))
            }
            val chapterIndex = rawChapterIndex.coerceAtLeast(0)
            val syncChapter = (chapterIndex - syncChapterOffset).coerceAtLeast(0)
            Log.d(TAG, "syncSelectedText: chapterIndex=$chapterIndex, syncChapter=$syncChapter, text='${selectedText.take(60)}'")

            lifecycleScope.launch {
                val audioMs = repository.epubToAudioText(pairId, syncChapter, selectedText, rewindMs = 2000)
                if (audioMs > 0) {
                    Log.d(TAG, "syncSelectedText: matched audioMs=$audioMs (${formatAudioTime(audioMs.toLong())})")
                    sentenceSyncPending = true
                    // Save to process-local state so the player can read it directly,
                    // bypassing the server round-trip race condition.
                    SyncState.pendingAudioSeekMs = audioMs.toLong()
                    repository.updateBookmark(
                        pairId = pairId,
                        source = "ebook",
                        epubChapter = syncChapter,
                        audioPositionMs = audioMs,
                    )
                    val timeStr = formatAudioTime(audioMs.toLong())
                    android.widget.Toast.makeText(this@ReaderActivity, "Audio synced to $timeStr", android.widget.Toast.LENGTH_SHORT).show()
                } else {
                    android.widget.Toast.makeText(this@ReaderActivity, "No matching audio found", android.widget.Toast.LENGTH_SHORT).show()
                }
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
