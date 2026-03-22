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
                val chapterIdx = findSpineIndexForText(previewText, syncChapter)
                    ?: syncChapter.takeIf { it in pub.readingOrder.indices }
                Log.d(TAG, "getInitialLocator: syncChapter=$syncChapter, resolved chapterIdx=$chapterIdx")
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
        val bookTitle = pair?.title
        if (!bookTitle.isNullOrEmpty()) {
            val titlePattern = Regex("^${Regex.escape(bookTitle)}\\s*", RegexOption.IGNORE_CASE)
            text = titlePattern.replace(text, "") // Remove first occurrence
            text = titlePattern.replace(text, "") // Remove possible second occurrence
            text = text.trim()
        }
        return text
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
                Log.d(TAG, "savePosition: locator.href='${locator.href}', rawIndex=$rawChapterIndex, chapterIndex=$chapterIndex, progression=${locator.locations.progression}")

                val progression = locator.locations.progression ?: 0.0
                val textPreview = extractTextPreview(chapterIndex, progression)
                
                val locatorJson = locator.toJSON().toString()

            if (sentenceSyncPending) {
                // Don't overwrite the sentence-level audio bookmark;
                // just save the epub locator so we can restore the page position.
                // Flag stays true for the rest of the activity lifecycle.
                Log.d(TAG, "savePosition: sentenceSyncPending=true, skipping audio sync")
                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = chapterIndex,
                    epubLocator = locatorJson,
                )
            } else {
                val syncPoint = repository.getSyncPointForEpubText(pairId, chapterIndex, textPreview)
                if (syncPoint != null) {
                    repository.updateBookmark(
                        pairId = pairId,
                        source = "ebook",
                        epubChapter = syncPoint.epubChapter,
                        epubSentenceIndex = syncPoint.epubSentenceIndex,
                        audioPositionMs = syncPoint.audioStartMs,
                        epubLocator = locatorJson,
                    )
                } else {
                    // Only save epub locator — don't corrupt audio position
                    Log.w(TAG, "savePosition: no sync match found, saved epub locator only")
                    repository.updateBookmark(
                        pairId = pairId,
                        source = "ebook",
                        epubChapter = chapterIndex,
                        epubLocator = locatorJson,
                    )
                }
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

        val progression = locator.locations.progression ?: 0.0
        Log.d(TAG, "syncAudioToPage called! locator.href='${locator.href}', progression=$progression")
        Log.d(TAG, "syncAudioToPage: rawChapterIndex=$rawChapterIndex => chapterIndex=$chapterIndex")

        lifecycleScope.launch {
            val textPreview = extractTextPreview(chapterIndex, progression)

            val audioMs = repository.epubToAudioText(pairId, chapterIndex, textPreview, rewindMs = 2000)
            if (audioMs > 0) {
                // Save audio position as bookmark so the player picks it up
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

        webView.evaluateJavascript("""
            (function() {
                var sel = window.getSelection().toString();
                if (!sel) {
                    var frames = document.querySelectorAll('iframe');
                    for (var i = 0; i < frames.length; i++) {
                        try { sel = frames[i].contentWindow.getSelection().toString(); } catch(e) {}
                        if (sel) break;
                    }
                }
                return sel || '';
            })()
        """.trimIndent()) { result ->
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
            Log.d(TAG, "syncSelectedText: chapterIndex=$chapterIndex, text='${selectedText.take(60)}'")

            lifecycleScope.launch {
                val audioMs = repository.epubToAudioText(pairId, chapterIndex, selectedText, rewindMs = 2000)
                if (audioMs > 0) {
                    Log.d(TAG, "syncSelectedText: matched audioMs=$audioMs (${formatAudioTime(audioMs.toLong())})")
                    sentenceSyncPending = true
                    // Save to process-local state so the player can read it directly,
                    // bypassing the server round-trip race condition.
                    SyncState.pendingAudioSeekMs = audioMs.toLong()
                    repository.updateBookmark(
                        pairId = pairId,
                        source = "ebook",
                        epubChapter = chapterIndex,
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
