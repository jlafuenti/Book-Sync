package com.booksync.ui.reader

import android.os.Bundle
import android.util.Log
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.SeekBar
import android.widget.TextView
import org.readium.r2.navigator.preferences.Theme
import androidx.appcompat.app.AppCompatActivity
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.booksync.R
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
        const val EXTRA_PAIR_ID = "pair_id"
        const val RESULT_SWITCH_TO_AUDIO = 42
        private const val TAG = "ReaderActivity"
        private const val NAV_FRAGMENT_TAG = "EpubNavigatorFragment"
        private const val SAVE_INTERVAL_MS = 5000L
        private const val PREFS_NAME = "reader_display"
        private const val KEY_FONT_SIZE = "font_size"
        private const val KEY_THEME = "theme"
    }

    @Inject lateinit var repository: BookSyncRepository

    private var publication: Publication? = null
    private var navigator: EpubNavigatorFragment? = null
    private var pair: BookPairEntity? = null
    private var pairId: Int = 0
    private var positionSaveJob: Job? = null
    private var isBarVisible = false
    private var isSeeking = false

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
                R.id.action_toc -> { showTableOfContents(); true }
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

                val initialLocator = getInitialLocator()
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

    private suspend fun getInitialLocator(): Locator? {
        return try {
            val bookmark = repository.getBookmarkFlow(pairId).firstOrNull()
            Log.d(TAG, "Bookmark loaded: epubLocator=${bookmark?.epubLocator?.take(80)}")
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
                val chapterIndex = pub.readingOrder.indexOfFirst { it.href == locator.href }.coerceAtLeast(0)
                val locatorJson = locator.toJSON().toString()

                repository.updateBookmark(
                    pairId = pairId,
                    source = "ebook",
                    epubChapter = chapterIndex,
                    epubSentenceIndex = ((locator.locations.totalProgression ?: 0.0) * 1000).toInt(),
                    epubLocator = locatorJson,
                )
            } catch (e: Exception) {
                Log.w(TAG, "Error saving position", e)
            }
        }
    }

    // ============ Table of Contents ============

    private fun showTableOfContents() {
        val pub = publication ?: return
        val toc = pub.tableOfContents
        if (toc.isEmpty()) {
            Log.w(TAG, "No table of contents in this publication")
            return
        }

        val titles = toc.map { it.title ?: "Untitled" }.toTypedArray()

        MaterialAlertDialogBuilder(this)
            .setTitle("Table of Contents")
            .setItems(titles) { _, which ->
                val link = toc[which]
                val locator = pub.locatorFromLink(link) ?: return@setItems
                navigator?.go(locator, animated = true)
                toggleBars() // hide bars after selection
            }
            .setNegativeButton("Cancel", null)
            .show()
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
        currentPreferences = EpubPreferences(fontSize = fontSize, theme = theme)
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
        val btnLight = dialogView.findViewById<View>(R.id.btn_theme_light)
        val btnSepia = dialogView.findViewById<View>(R.id.btn_theme_sepia)
        val btnDark = dialogView.findViewById<View>(R.id.btn_theme_dark)

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

        btnLight.setOnClickListener {
            currentPreferences = currentPreferences.copy(theme = Theme.LIGHT)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        btnSepia.setOnClickListener {
            currentPreferences = currentPreferences.copy(theme = Theme.SEPIA)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }
        btnDark.setOnClickListener {
            currentPreferences = currentPreferences.copy(theme = Theme.DARK)
            nav.submitPreferences(currentPreferences)
            savePreferences()
        }

        dialog.show()
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
